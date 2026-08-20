from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.gateway import (
    build_gateway_runtime_pool,
    GatewayProfile,
    GatewayProfileRegistry,
    GatewayRuntimePool,
    load_gateway_config,
    normalize_instance_path,
)
from agent_runtime.oauth import OAuthConfig
from agent_runtime.route_probe import (
    ROUTE_PROBE_HEADER,
    ROUTE_PROBE_PATH,
    ROUTE_PROBE_TOKEN_ENV,
    workspace_fingerprint,
)
from agent_runtime.runtime import Runtime
from agent_runtime.server import MCPHTTPServer


class GatewayFrameworkTests(unittest.TestCase):
    def test_instance_path_normalization_and_reserved_prefix(self) -> None:
        self.assertEqual(normalize_instance_path(""), "")
        self.assertEqual(normalize_instance_path("/"), "")
        self.assertEqual(normalize_instance_path(" company/ "), "/company")
        self.assertEqual(normalize_instance_path("/team/dev/"), "/team/dev")
        with self.assertRaises(ValueError):
            normalize_instance_path("/.well-known/company")

    def test_root_profile_is_gateway_fallback_without_changing_root_mcp_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = GatewayProfileRegistry()
            root_profile = registry.register(GatewayProfile("root", "", root / "root"))
            child = registry.register(GatewayProfile("child", "/child", root / "child"))

            self.assertEqual(registry.resolve("/mcp").profile, root_profile)
            self.assertEqual(registry.resolve("/oauth/token").profile, root_profile)
            self.assertEqual(registry.resolve("/child/mcp").profile, child)
            self.assertEqual(
                registry.resolve("/.well-known/oauth-authorization-server").profile,
                root_profile,
            )

    def test_registry_rejects_duplicate_profile_id_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = GatewayProfileRegistry()
            registry.register(GatewayProfile("a", "/company", root))

            with self.assertRaisesRegex(ValueError, "profile_id"):
                registry.register(GatewayProfile("a", "/home", root))
            with self.assertRaisesRegex(ValueError, "instance_path"):
                registry.register(GatewayProfile("b", "/company", root))

    def test_direct_and_oauth_metadata_paths_resolve_to_same_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = GatewayProfileRegistry()
            profile = registry.register(
                GatewayProfile("company", "/company", Path(temporary))
            )

            cases = {
                "/company/mcp": "profile",
                "/company/oauth/authorize": "profile",
                "/company/oauth/token": "profile",
                "/company/oauth/register": "profile",
                "/.well-known/oauth-authorization-server/company": "oauth_authorization_metadata",
                "/.well-known/openid-configuration/company": "oauth_authorization_metadata",
                "/company/.well-known/openid-configuration": "oauth_authorization_metadata",
                "/.well-known/oauth-protected-resource/company/mcp": "oauth_protected_resource_metadata",
            }
            for path, kind in cases.items():
                with self.subTest(path=path):
                    route = registry.resolve(path)
                    self.assertIsNotNone(route)
                    assert route is not None
                    self.assertEqual(route.profile, profile)
                    self.assertEqual(route.kind, kind)

    def test_longest_instance_path_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = GatewayProfileRegistry()
            registry.register(GatewayProfile("team", "/team", root / "team"))
            nested = registry.register(
                GatewayProfile("team-dev", "/team/dev", root / "team-dev")
            )

            route = registry.resolve("/team/dev/mcp")
            self.assertIsNotNone(route)
            assert route is not None
            self.assertEqual(route.profile, nested)

    def test_runtime_pool_isolates_runtime_and_permission_session_per_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            company_root = root / "company"
            home_root = root / "home"
            company_root.mkdir()
            home_root.mkdir()
            registry = GatewayProfileRegistry()
            company = registry.register(
                GatewayProfile("company", "/company", company_root)
            )
            home = registry.register(GatewayProfile("home", "/home", home_root))
            pool = GatewayRuntimePool(registry)
            try:
                company_runtime = pool.get(company.profile_id)
                home_runtime = pool.get(home.profile_id)

                self.assertIs(company_runtime, pool.get(company.profile_id))
                self.assertIsNot(company_runtime, home_runtime)
                self.assertIsNot(
                    company_runtime.permission_session,
                    home_runtime.permission_session,
                )
                self.assertEqual(company_runtime.workspace.root, company_root.resolve())
                self.assertEqual(home_runtime.workspace.root, home_root.resolve())
                resolved = pool.runtime_for_path("/home/mcp")
                self.assertIsNotNone(resolved)
                assert resolved is not None
                self.assertEqual(resolved[0], home)
                self.assertIs(resolved[1], home_runtime)
            finally:
                pool.close()

    def test_http_gateway_routes_profiles_and_oauth_metadata_on_one_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            company_root = root / "company"
            home_root = root / "home"
            company_root.mkdir()
            home_root.mkdir()
            registry = GatewayProfileRegistry()
            registry.register(GatewayProfile("company", "/company", company_root))
            registry.register(GatewayProfile("home", "/home", home_root))

            def factory(profile: GatewayProfile) -> Runtime:
                oauth = OAuthConfig(
                    password="password",
                    server_url=f"https://mcp.example.com{profile.instance_path}",
                    token_secret=b"x" * 32,
                )
                return Runtime(
                    profile.workspace,
                    permission_mode=profile.permission_mode,
                    oauth_config=oauth,
                )

            pool = GatewayRuntimePool(registry, factory=factory)
            server = MCPHTTPServer(("127.0.0.1", 0), gateway_pool=pool)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = int(server.server_address[1])
            try:
                for instance in ("company", "home"):
                    with self.subTest(instance=instance):
                        connection = http.client.HTTPConnection("127.0.0.1", port)
                        connection.request("GET", f"/{instance}/")
                        response = connection.getresponse()
                        payload = json.loads(response.read())
                        self.assertEqual(response.status, 200)
                        self.assertEqual(
                            payload["transport"]["endpoint"],
                            f"/{instance}/mcp",
                        )
                        connection.close()

                        connection = http.client.HTTPConnection("127.0.0.1", port)
                        connection.request(
                            "GET",
                            f"/.well-known/oauth-authorization-server/{instance}",
                        )
                        response = connection.getresponse()
                        metadata = json.loads(response.read())
                        self.assertEqual(response.status, 200)
                        self.assertEqual(
                            metadata["issuer"],
                            f"https://mcp.example.com/{instance}",
                        )
                        connection.close()

                        with patch.dict(
                            os.environ,
                            {ROUTE_PROBE_TOKEN_ENV: "gateway-probe-secret"},
                        ):
                            connection = http.client.HTTPConnection(
                                "127.0.0.1",
                                port,
                            )
                            connection.request(
                                "GET",
                                f"/{instance}{ROUTE_PROBE_PATH}",
                                headers={
                                    ROUTE_PROBE_HEADER: "gateway-probe-secret"
                                },
                            )
                            response = connection.getresponse()
                            probe = json.loads(response.read())
                            self.assertEqual(response.status, 200)
                            expected_root = (
                                company_root if instance == "company" else home_root
                            )
                            self.assertEqual(
                                probe["workspace_fingerprint"],
                                workspace_fingerprint(expected_root),
                            )
                            connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", port)
                connection.request("GET", "/unknown/mcp")
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 404)
                self.assertEqual(payload["error"], "gateway_profile_not_found")
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                pool.close()
                thread.join(timeout=2)

    def test_gateway_config_builds_isolated_oauth_runtime_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            company_root = root / "company"
            home_root = root / "home"
            company_root.mkdir()
            home_root.mkdir()
            config_file = root / "gateway.json"
            config_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "profiles": [
                            {
                                "profile_id": "company",
                                "instance_path": "/company",
                                "workspace": str(company_root),
                                "permission_mode": "safe",
                                "oauth": {
                                    "password": "company-password",
                                    "server_url": "https://mcp.example.com/company",
                                    "token_secret_hex": "11" * 32,
                                    "registry_file": str(root / "oauth-company.json"),
                                },
                            },
                            {
                                "profile_id": "home",
                                "instance_path": "/home",
                                "workspace": str(home_root),
                                "permission_mode": "trusted",
                                "oauth": {
                                    "password": "home-password",
                                    "server_url": "https://mcp.example.com/home",
                                    "token_secret_hex": "22" * 32,
                                    "registry_file": str(root / "oauth-home.json"),
                                },
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            config = load_gateway_config(config_file)
            registry, pool = build_gateway_runtime_pool(config)
            try:
                company = pool.get("company")
                home = pool.get("home")
                self.assertEqual(len(registry), 2)
                self.assertEqual(company.oauth_config.issuer, "https://mcp.example.com/company")
                self.assertEqual(home.oauth_config.issuer, "https://mcp.example.com/home")
                self.assertNotEqual(
                    company.oauth_config.token_secret,
                    home.oauth_config.token_secret,
                )
                self.assertNotEqual(
                    company.oauth_config.registry.persistence_file,
                    home.oauth_config.registry.persistence_file,
                )
                self.assertEqual(company.permission_mode, "safe")
                self.assertEqual(home.permission_mode, "trusted")
            finally:
                pool.close()

    def test_gateway_config_rejects_issuer_path_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            config_file = root / "gateway.json"
            config_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "profiles": [
                            {
                                "profile_id": "company",
                                "instance_path": "/company",
                                "workspace": str(workspace),
                                "oauth": {
                                    "password": "password",
                                    "server_url": "https://mcp.example.com/home",
                                    "token_secret_hex": "11" * 32,
                                    "registry_file": str(root / "oauth.json"),
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "does not match instance_path"):
                load_gateway_config(config_file)

    def test_gateway_config_creates_profile_specific_broker_clients(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            company_root = root / "company"
            home_root = root / "home"
            broker_dir = root / "broker"
            company_root.mkdir()
            home_root.mkdir()
            broker_dir.mkdir()
            config_file = root / "gateway.json"
            shared_secret = "33" * 32
            config_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "profiles": [
                            {
                                "profile_id": "company",
                                "instance_path": "/company",
                                "workspace": str(company_root),
                                "oauth": {
                                    "password": "company-password",
                                    "server_url": "https://mcp.example.com/company",
                                    "token_secret_hex": "11" * 32,
                                    "registry_file": str(root / "company-oauth.json"),
                                },
                                "permission_broker": {
                                    "directory": str(broker_dir),
                                    "secret_hex": shared_secret,
                                    "server_id": "company",
                                },
                            },
                            {
                                "profile_id": "home",
                                "instance_path": "/home",
                                "workspace": str(home_root),
                                "oauth": {
                                    "password": "home-password",
                                    "server_url": "https://mcp.example.com/home",
                                    "token_secret_hex": "22" * 32,
                                    "registry_file": str(root / "home-oauth.json"),
                                },
                                "permission_broker": {
                                    "directory": str(broker_dir),
                                    "secret_hex": shared_secret,
                                    "server_id": "home",
                                },
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            registry, pool = build_gateway_runtime_pool(load_gateway_config(config_file))
            try:
                self.assertEqual(len(registry), 2)
                company = pool.get("company").local_permission_broker
                home = pool.get("home").local_permission_broker
                self.assertIsNotNone(company)
                self.assertIsNotNone(home)
                assert company is not None and home is not None
                self.assertIsNot(company, home)
                self.assertEqual(company.server_id, "company")
                self.assertEqual(home.server_id, "home")
                self.assertEqual(company.directory, home.directory)
            finally:
                pool.close()


if __name__ == "__main__":
    unittest.main()

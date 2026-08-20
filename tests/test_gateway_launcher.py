from __future__ import annotations

import os
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from agent_workbench.config import LaunchInfo, NetworkConfig
from agent_workbench.gateway_launcher import (
    GatewayLaunchConfig,
    GatewayLaunchInfo,
    GatewayProfileLaunchInfo,
    MCPGatewayLauncher,
)
from agent_workbench.gateway_process import GatewayChildProfile
from agent_workbench.network.base import NetworkProviderResult
from agent_runtime.local_permission_broker import (
    BROKER_DIR_ENV,
    BROKER_SECRET_ENV,
    BROKER_SERVER_ID_ENV,
)
from agent_runtime.route_probe import ROUTE_PROBE_PATH, workspace_fingerprint


class _FakeProcess:
    returncode: int | None = None
    stopped = False

    def poll(self) -> int | None:
        return 0 if self.stopped else None

    def terminate(self) -> None:
        self.stopped = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self.stopped = True
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.stopped = True
        self.returncode = -9


class _FakeProvider:
    display_name = "Fake Provider"

    def __init__(self, public_base_url: str, mode_label: str = "Fake") -> None:
        self.public_base_url = public_base_url
        self.mode_label = mode_label
        self.started = False
        self.stopped = False

    @property
    def is_running(self) -> bool:
        return self.started and not self.stopped

    @property
    def exit_code(self) -> int | None:
        return None

    def start(self, host: str, port: int, config: NetworkConfig) -> NetworkProviderResult:
        self.started = True
        return NetworkProviderResult(
            provider=config.provider,
            public_base_url=self.public_base_url,
            mode_label=self.mode_label,
        )

    def stop(self) -> None:
        self.stopped = True


class GatewayLauncherTests(unittest.TestCase):
    def _profiles(self, root: Path) -> tuple[GatewayChildProfile, ...]:
        company = root / "company"
        home = root / "home"
        company.mkdir()
        home.mkdir()
        return (
            GatewayChildProfile(
                server_id="company",
                name="Company",
                workspace=company,
                oauth_password="company-password",
                instance_path="/company",
            ),
            GatewayChildProfile(
                server_id="home",
                name="Home",
                workspace=home,
                oauth_password="home-password",
                instance_path="/home",
            ),
        )

    def test_named_gateway_builds_multiple_profile_urls_on_one_hostname(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profiles = self._profiles(Path(temporary))
            provider = _FakeProvider("https://mcp.example.com", "Cloudflare Named Tunnel")
            launcher = MCPGatewayLauncher()
            captured: dict[str, object] = {}

            def fake_gateway_start(config, env):
                captured["config"] = config
                captured["env"] = env
                launcher._gateway.process = _FakeProcess()  # type: ignore[assignment]

            with (
                patch(
                    "agent_workbench.gateway_launcher.create_network_provider",
                    return_value=provider,
                ),
                patch("agent_workbench.gateway_launcher.check_port_available"),
                patch.object(launcher._gateway, "start", side_effect=fake_gateway_start),
            ):
                info = launcher.start(
                    GatewayLaunchConfig(
                        network=NetworkConfig(
                            provider="cloudflare",
                            public_url="https://mcp.example.com",
                            options={"tunnel_token": "token"},
                        ),
                        profiles=profiles,
                    )
                )
                try:
                    self.assertEqual(info.public_base_url, "https://mcp.example.com")
                    self.assertEqual(
                        info.profile("company").public_mcp_url,  # type: ignore[union-attr]
                        "https://mcp.example.com/company/mcp",
                    )
                    self.assertEqual(
                        info.profile("home").public_mcp_url,  # type: ignore[union-attr]
                        "https://mcp.example.com/home/mcp",
                    )
                    child_config = captured["config"]
                    self.assertEqual(
                        [profile.lifecycle for profile in child_config.profiles],  # type: ignore[attr-defined]
                        ["persistent", "persistent"],
                    )
                finally:
                    launcher.stop()

            self.assertTrue(provider.stopped)

    def test_quick_tunnel_forces_profile_oauth_lifecycle_ephemeral(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profiles = self._profiles(Path(temporary))
            provider = _FakeProvider("https://random.trycloudflare.com", "Cloudflare Quick Tunnel")
            launcher = MCPGatewayLauncher()
            captured: dict[str, object] = {}

            def fake_gateway_start(config, env):
                captured["config"] = config
                launcher._gateway.process = _FakeProcess()  # type: ignore[assignment]

            with (
                patch(
                    "agent_workbench.gateway_launcher.create_network_provider",
                    return_value=provider,
                ),
                patch("agent_workbench.gateway_launcher.check_port_available"),
                patch.object(launcher._gateway, "start", side_effect=fake_gateway_start),
            ):
                info = launcher.start(
                    GatewayLaunchConfig(
                        network=NetworkConfig(provider="cloudflare"),
                        profiles=profiles,
                    )
                )
                try:
                    child_config = captured["config"]
                    self.assertEqual(
                        [profile.lifecycle for profile in child_config.profiles],  # type: ignore[attr-defined]
                        ["ephemeral", "ephemeral"],
                    )
                    self.assertEqual(
                        [profile.lifecycle for profile in info.profiles],
                        ["ephemeral", "ephemeral"],
                    )
                finally:
                    launcher.stop()

    def test_gateway_child_environment_removes_single_profile_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profiles = self._profiles(Path(temporary))
            provider = _FakeProvider("https://mcp.example.com")
            launcher = MCPGatewayLauncher()
            captured: dict[str, object] = {}

            def fake_gateway_start(config, env):
                captured["env"] = env
                launcher._gateway.process = _FakeProcess()  # type: ignore[assignment]

            polluted = {
                "AGENT_RUNTIME_OAUTH_PASSWORD": "old-password",
                "AGENT_RUNTIME_SERVER_URL": "https://old.example.com",
                "AGENT_RUNTIME_OAUTH_TOKEN_SECRET": "11" * 32,
                "AGENT_RUNTIME_OAUTH_CLIENT_REGISTRY_FILE": "/tmp/old.json",
                BROKER_DIR_ENV: "/tmp/old-broker",
                BROKER_SECRET_ENV: "22" * 32,
                BROKER_SERVER_ID_ENV: "old-server",
            }
            with (
                patch.dict(os.environ, polluted),
                patch(
                    "agent_workbench.gateway_launcher.create_network_provider",
                    return_value=provider,
                ),
                patch("agent_workbench.gateway_launcher.check_port_available"),
                patch.object(launcher._gateway, "start", side_effect=fake_gateway_start),
            ):
                launcher.start(
                    GatewayLaunchConfig(
                        network=NetworkConfig(
                            provider="cloudflare",
                            public_url="https://mcp.example.com",
                            options={"tunnel_token": "token"},
                        ),
                        profiles=profiles,
                    )
                )
                try:
                    child_env = captured["env"]
                    for name in polluted:
                        self.assertNotIn(name, child_env)  # type: ignore[operator]
                finally:
                    launcher.stop()

    def test_gateway_fixed_public_url_rejects_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profiles = self._profiles(Path(temporary))
            with self.assertRaisesRegex(ValueError, "hostname"):
                GatewayLaunchConfig(
                    network=NetworkConfig(
                        provider="cloudflare",
                        public_url="https://mcp.example.com/shared",
                        options={"tunnel_token": "token"},
                    ),
                    profiles=profiles,
                ).validated()

    def test_root_profile_keeps_original_mcp_and_oauth_urls_when_children_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "root"
            child_workspace = root / "child"
            workspace.mkdir()
            child_workspace.mkdir()
            profiles = (
                GatewayChildProfile(
                    server_id="root",
                    name="Root",
                    workspace=workspace,
                    oauth_password="root-password",
                    instance_path="",
                ),
                GatewayChildProfile(
                    server_id="child",
                    name="Child",
                    workspace=child_workspace,
                    oauth_password="child-password",
                    instance_path="/child",
                ),
            )
            provider = _FakeProvider("https://mcp.example.com", "Cloudflare Named Tunnel")
            launcher = MCPGatewayLauncher()

            def fake_gateway_start(_config, _env):
                launcher._gateway.process = _FakeProcess()  # type: ignore[assignment]

            with (
                patch(
                    "agent_workbench.gateway_launcher.create_network_provider",
                    return_value=provider,
                ),
                patch("agent_workbench.gateway_launcher.check_port_available"),
                patch.object(launcher._gateway, "start", side_effect=fake_gateway_start),
                patch.object(launcher, "_diagnose_background"),
            ):
                info = launcher.start(
                    GatewayLaunchConfig(
                        network=NetworkConfig(
                            provider="cloudflare",
                            public_url="https://mcp.example.com",
                            options={"tunnel_token": "token"},
                        ),
                        profiles=profiles,
                    )
                )
                try:
                    root_info = info.profile("root")
                    child_info = info.profile("child")
                    self.assertEqual(root_info.public_mcp_url, "https://mcp.example.com/mcp")  # type: ignore[union-attr]
                    self.assertEqual(root_info.oauth_issuer, "https://mcp.example.com")  # type: ignore[union-attr]
                    self.assertEqual(child_info.public_mcp_url, "https://mcp.example.com/child/mcp")  # type: ignore[union-attr]
                finally:
                    launcher.stop()

    def test_gateway_diagnostic_verifies_runtime_and_oauth_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "company"
            workspace.mkdir()
            launcher = MCPGatewayLauncher()
            provider = _FakeProvider("https://mcp.example.com")
            provider.started = True
            launcher._provider = provider
            launcher._gateway.process = _FakeProcess()  # type: ignore[assignment]
            profile = GatewayProfileLaunchInfo(
                server_id="company",
                name="Company",
                workspace=workspace,
                instance_path="/company",
                local_mcp_url="http://127.0.0.1:8234/company/mcp",
                public_mcp_url="https://mcp.example.com/company/mcp",
                oauth_issuer="https://mcp.example.com/company",
                lifecycle="persistent",
            )
            launcher._info = GatewayLaunchInfo(
                host="127.0.0.1",
                port=8234,
                public_base_url="https://mcp.example.com",
                tunnel_url="https://mcp.example.com",
                url_mode="Cloudflare Named Tunnel",
                profiles=(profile,),
            )
            launcher._route_probe_token = "probe-secret"
            fingerprint = workspace_fingerprint(workspace)

            def fake_json_get(url: str, **_kwargs):
                if ROUTE_PROBE_PATH in url:
                    return {"ok": True, "workspace_fingerprint": fingerprint}, {}
                if url.endswith("/company/"):
                    return {
                        "transport": {
                            "type": "streamable_http",
                            "endpoint": "/company/mcp",
                        }
                    }, {}
                if "oauth-authorization-server" in url:
                    return {
                        "issuer": "https://mcp.example.com/company",
                        "authorization_endpoint": "https://mcp.example.com/company/oauth/authorize",
                        "token_endpoint": "https://mcp.example.com/company/oauth/token",
                    }, {}
                if "oauth-protected-resource" in url:
                    return {
                        "resource": "https://mcp.example.com/company/mcp",
                        "authorization_servers": ["https://mcp.example.com/company"],
                    }, {}
                raise AssertionError(f"unexpected diagnostic URL: {url}")

            headers = Message()
            headers["WWW-Authenticate"] = (
                'Bearer realm="micromatrix-workbench", '
                'resource_metadata="https://mcp.example.com/.well-known/'
                'oauth-protected-resource/company/mcp"'
            )
            unauthorized = urllib.error.HTTPError(
                profile.public_mcp_url,
                401,
                "Unauthorized",
                headers,
                None,
            )

            with (
                patch.object(launcher, "_json_get", side_effect=fake_json_get),
                patch(
                    "agent_workbench.gateway_launcher.urllib.request.urlopen",
                    side_effect=unauthorized,
                ),
            ):
                report = launcher.diagnose()

            self.assertTrue(report.ok)
            self.assertEqual(len(report.profiles), 1)
            self.assertEqual(
                set(report.profiles[0].checks),
                {
                    "local_path_runtime",
                    "public_path_runtime",
                    "server_card",
                    "oauth_authorization_metadata",
                    "oauth_protected_resource",
                    "mcp_auth_challenge",
                },
            )

    def test_single_mode_uses_only_root_runtime_even_when_child_profiles_are_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root_workspace = root / "root"
            child_workspace = root / "child"
            root_workspace.mkdir()
            child_workspace.mkdir()
            launcher = MCPGatewayLauncher()
            config = GatewayLaunchConfig(
                network=NetworkConfig(
                    provider="external",
                    public_url="https://mcp.example.com",
                    options={},
                ),
                profiles=(
                    GatewayChildProfile(
                        server_id="root",
                        name="Root",
                        workspace=root_workspace,
                        oauth_password="root-password",
                        instance_path="",
                        allow_network=True,
                        enable_view_image=False,
                    ),
                    GatewayChildProfile(
                        server_id="child",
                        name="Child",
                        workspace=child_workspace,
                        oauth_password="child-password",
                        instance_path="/child",
                    ),
                ),
                mode="single",
            )
            direct_info = LaunchInfo(
                workspace=root_workspace,
                local_mcp_url="http://127.0.0.1:8234/mcp",
                tunnel_url="https://mcp.example.com",
                public_base_url="https://mcp.example.com",
                public_mcp_url="https://mcp.example.com/mcp",
                url_mode="External",
            )

            with (
                patch.object(
                    launcher._direct,
                    "start",
                    return_value=direct_info,
                ) as direct_start,
                patch.object(launcher._gateway, "start") as gateway_start,
            ):
                info = launcher.start(config)

            gateway_start.assert_not_called()
            direct_start.assert_called_once()
            direct_config = direct_start.call_args.args[0]
            self.assertEqual(direct_config.workspace, root_workspace.resolve())
            self.assertTrue(direct_config.allow_network)
            self.assertFalse(direct_config.enable_view_image)
            self.assertEqual(
                [profile.server_id for profile in info.profiles],
                ["root"],
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_workbench.gateway_process import (
    GatewayChildProfile,
    GatewayProcessConfig,
    build_gateway_mcp_command,
    prepare_gateway_config,
)
from agent_workbench.oauth_persistence import OAuthPersistence
from agent_workbench.permission_broker import DesktopPermissionBroker


class GatewayProcessTests(unittest.TestCase):
    def test_gateway_process_config_requires_hostname_only_public_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            profile = GatewayChildProfile(
                server_id="company",
                name="Company",
                workspace=workspace,
                oauth_password="password",
                instance_path="/company",
            )
            with self.assertRaisesRegex(ValueError, "hostname"):
                GatewayProcessConfig(
                    public_base_url="https://mcp.example.com/shared",
                    profiles=(profile,),
                ).validated()

    def test_prepare_gateway_config_builds_per_profile_issuers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            company_root = root / "company"
            home_root = root / "home"
            company_root.mkdir()
            home_root.mkdir()
            company_persistence = OAuthPersistence(
                registry_file=root / "company-clients.json",
                token_secret_hex="11" * 32,
            )
            home_persistence = OAuthPersistence(
                registry_file=root / "home-clients.json",
                token_secret_hex="22" * 32,
            )
            config = GatewayProcessConfig(
                public_base_url="https://mcp.example.com",
                profiles=(
                    GatewayChildProfile(
                        server_id="company",
                        name="Company",
                        workspace=company_root,
                        oauth_password="company-password",
                        instance_path="company",
                    ),
                    GatewayChildProfile(
                        server_id="home",
                        name="Home",
                        workspace=home_root,
                        oauth_password="home-password",
                        instance_path="/home/",
                    ),
                ),
            )

            persistence_by_issuer = {
                "https://mcp.example.com/company": company_persistence,
                "https://mcp.example.com/home": home_persistence,
            }

            def persistence_for_issuer(issuer: str) -> OAuthPersistence:
                return persistence_by_issuer[issuer]

            with (
                patch(
                    "agent_workbench.gateway_process.prepare_issuer_oauth_persistence",
                    side_effect=persistence_for_issuer,
                ),
                patch("agent_workbench.gateway_process.bind_server_oauth_issuer"),
            ):
                prepared = prepare_gateway_config(config)
            try:
                payload = json.loads(prepared.config_file.read_text(encoding="utf-8"))
                self.assertEqual(payload["version"], 1)
                self.assertEqual(len(payload["profiles"]), 2)
                by_id = {item["profile_id"]: item for item in payload["profiles"]}
                self.assertEqual(
                    by_id["company"]["oauth"]["server_url"],
                    "https://mcp.example.com/company",
                )
                self.assertEqual(
                    by_id["home"]["oauth"]["server_url"],
                    "https://mcp.example.com/home",
                )
                self.assertEqual(
                    by_id["company"]["oauth"]["registry_file"],
                    str(company_persistence.registry_file),
                )
                self.assertEqual(
                    by_id["home"]["oauth"]["registry_file"],
                    str(home_persistence.registry_file),
                )
                if os.name != "nt":
                    self.assertEqual(prepared.config_file.stat().st_mode & 0o777, 0o600)
            finally:
                prepared.cleanup()

    def test_gateway_command_uses_single_gateway_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = GatewayChildProfile(
                server_id="company",
                name="Company",
                workspace=root,
                oauth_password="password",
                instance_path="/company",
            )
            config = GatewayProcessConfig(
                public_base_url="https://mcp.example.com",
                profiles=(profile,),
                host="127.0.0.1",
                port=8234,
            )
            config_file = root / "gateway.json"
            with patch("agent_workbench.gateway_process.is_frozen", return_value=False):
                command = build_gateway_mcp_command(config, config_file)

            self.assertIn("agent_workbench.mcp_worker", command)
            self.assertIn("--gateway-config", command)
            self.assertIn(str(config_file), command)
            self.assertIn("8234", command)

    def test_prepare_gateway_config_serializes_profile_specific_broker_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            company_root = root / "company"
            home_root = root / "home"
            company_root.mkdir()
            home_root.mkdir()
            company_persistence = OAuthPersistence(
                registry_file=root / "company-clients.json",
                token_secret_hex="11" * 32,
            )
            home_persistence = OAuthPersistence(
                registry_file=root / "home-clients.json",
                token_secret_hex="22" * 32,
            )
            config = GatewayProcessConfig(
                public_base_url="https://mcp.example.com",
                profiles=(
                    GatewayChildProfile(
                        server_id="company",
                        name="Company",
                        workspace=company_root,
                        oauth_password="company-password",
                        instance_path="/company",
                    ),
                    GatewayChildProfile(
                        server_id="home",
                        name="Home",
                        workspace=home_root,
                        oauth_password="home-password",
                        instance_path="/home",
                    ),
                ),
            )
            persistence_by_issuer = {
                "https://mcp.example.com/company": company_persistence,
                "https://mcp.example.com/home": home_persistence,
            }
            broker = DesktopPermissionBroker()
            try:
                with (
                    patch(
                        "agent_workbench.gateway_process.prepare_issuer_oauth_persistence",
                        side_effect=lambda issuer: persistence_by_issuer[issuer],
                    ),
                    patch("agent_workbench.gateway_process.bind_server_oauth_issuer"),
                ):
                    prepared = prepare_gateway_config(
                        config,
                        permission_broker=broker,
                    )
                try:
                    payload = json.loads(
                        prepared.config_file.read_text(encoding="utf-8")
                    )
                    by_id = {
                        item["profile_id"]: item
                        for item in payload["profiles"]
                    }
                    company_broker = by_id["company"]["permission_broker"]
                    home_broker = by_id["home"]["permission_broker"]
                    self.assertEqual(company_broker["server_id"], "company")
                    self.assertEqual(home_broker["server_id"], "home")
                    self.assertEqual(
                        company_broker["directory"],
                        home_broker["directory"],
                    )
                    self.assertEqual(
                        company_broker["secret_hex"],
                        home_broker["secret_hex"],
                    )
                finally:
                    prepared.cleanup()
            finally:
                broker.cleanup()


if __name__ == "__main__":
    unittest.main()

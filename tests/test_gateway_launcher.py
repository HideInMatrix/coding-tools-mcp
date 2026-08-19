from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_tools_launcher.config import NetworkConfig
from coding_tools_launcher.gateway_launcher import (
    GatewayLaunchConfig,
    MCPGatewayLauncher,
)
from coding_tools_launcher.gateway_process import GatewayChildProfile
from coding_tools_launcher.network.base import NetworkProviderResult
from mcp_tools_server.local_permission_broker import (
    BROKER_DIR_ENV,
    BROKER_SECRET_ENV,
    BROKER_SERVER_ID_ENV,
)


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
                    "coding_tools_launcher.gateway_launcher.create_network_provider",
                    return_value=provider,
                ),
                patch("coding_tools_launcher.gateway_launcher.check_port_available"),
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
                    "coding_tools_launcher.gateway_launcher.create_network_provider",
                    return_value=provider,
                ),
                patch("coding_tools_launcher.gateway_launcher.check_port_available"),
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
                "CODING_TOOLS_MCP_OAUTH_PASSWORD": "old-password",
                "CODING_TOOLS_MCP_SERVER_URL": "https://old.example.com",
                "CODING_TOOLS_MCP_OAUTH_TOKEN_SECRET": "11" * 32,
                "CODING_TOOLS_MCP_OAUTH_CLIENT_REGISTRY_FILE": "/tmp/old.json",
                BROKER_DIR_ENV: "/tmp/old-broker",
                BROKER_SECRET_ENV: "22" * 32,
                BROKER_SERVER_ID_ENV: "old-server",
            }
            with (
                patch.dict(os.environ, polluted),
                patch(
                    "coding_tools_launcher.gateway_launcher.create_network_provider",
                    return_value=provider,
                ),
                patch("coding_tools_launcher.gateway_launcher.check_port_available"),
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


if __name__ == "__main__":
    unittest.main()

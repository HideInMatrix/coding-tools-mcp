from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_tools_launcher.config import LaunchConfig, NetworkConfig
from coding_tools_launcher.network.external import ExternalUrlProvider
from coding_tools_launcher.network.factory import create_network_provider
from coding_tools_launcher.network.frp import FrpProvider
from coding_tools_launcher.network.ngrok import NgrokProvider


class NetworkConfigTests(unittest.TestCase):
    def test_launch_config_requires_only_oauth_password_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = LaunchConfig(
                workspace=Path(temporary),
                oauth_password="password",
                network=NetworkConfig(
                    provider="external",
                    public_url="https://mcp.example.com",
                ),
            ).validated()
        self.assertEqual(config.oauth_password, "password")
        self.assertFalse(hasattr(config, "oauth_client_id"))
        self.assertFalse(hasattr(config, "oauth_client_secret"))

    def test_public_url_is_normalized(self) -> None:
        config = NetworkConfig(
            provider="external",
            public_url="https://mcp.example.com/mcp/",
        ).validated()
        self.assertEqual(config.public_url, "https://mcp.example.com")

    def test_launch_config_keeps_provider_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = LaunchConfig(
                workspace=Path(temporary),
                oauth_password="password",
                network=NetworkConfig(
                    provider="frp",
                    public_url="https://mcp.example.com",
                    options={"config_file": " ./frpc.toml "},
                ),
            ).validated()
        self.assertEqual(config.network.provider, "frp")
        self.assertEqual(config.network.options["config_file"], "./frpc.toml")

    def test_from_env_builds_ngrok_provider_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = LaunchConfig.from_env(
                workspace=Path(temporary),
                env={
                    # Legacy values are intentionally ignored. OAuth clients
                    # are always created through Dynamic Client Registration.
                    "CODING_TOOLS_MCP_OAUTH_CLIENT_ID": "client",
                    "CODING_TOOLS_MCP_OAUTH_CLIENT_SECRET": "secret",
                    "CODING_TOOLS_MCP_OAUTH_PASSWORD": "password",
                    "CODING_TOOLS_MCP_NETWORK_PROVIDER": "ngrok",
                    "CODING_TOOLS_MCP_NGROK": "/opt/ngrok",
                    "CODING_TOOLS_MCP_NGROK_AUTHTOKEN": "token",
                },
            )
        self.assertEqual(config.network.provider, "ngrok")
        self.assertEqual(config.network.options["executable"], "/opt/ngrok")
        self.assertEqual(config.network.options["authtoken"], "token")
        self.assertFalse(hasattr(config, "oauth_client_id"))
        self.assertFalse(hasattr(config, "oauth_client_secret"))

    def test_from_env_allows_dynamic_client_registration_without_client_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = LaunchConfig.from_env(
                workspace=Path(temporary),
                env={
                    "CODING_TOOLS_MCP_OAUTH_PASSWORD": "password",
                    "CODING_TOOLS_MCP_NETWORK_PROVIDER": "external",
                    "CODING_TOOLS_MCP_SERVER_URL": "https://mcp.example.com",
                },
            )
        self.assertFalse(hasattr(config, "oauth_client_id"))
        self.assertFalse(hasattr(config, "oauth_client_secret"))


class ProviderTests(unittest.TestCase):
    def test_factory_returns_requested_provider(self) -> None:
        provider = create_network_provider("external", lambda _message: None)
        self.assertIsInstance(provider, ExternalUrlProvider)

    def test_external_provider_has_no_child_process(self) -> None:
        logs: list[str] = []
        provider = ExternalUrlProvider(logs.append)
        result = provider.start(
            "127.0.0.1",
            8234,
            NetworkConfig(provider="external", public_url="https://mcp.example.com").validated(),
        )
        self.assertTrue(provider.is_running)
        self.assertEqual(result.public_base_url, "https://mcp.example.com")
        provider.stop()
        self.assertFalse(provider.is_running)

    def test_frp_requires_public_url_and_config_file(self) -> None:
        provider = FrpProvider(lambda _message: None)
        with self.assertRaisesRegex(ValueError, "Public URL"):
            provider.start("127.0.0.1", 8234, NetworkConfig(provider="frp"))

        with self.assertRaisesRegex(ValueError, "配置文件"):
            with patch.object(provider, "resolve_executable", return_value=Path("frpc")):
                provider.start(
                    "127.0.0.1",
                    8234,
                    NetworkConfig(
                        provider="frp",
                        public_url="https://mcp.example.com",
                    ).validated(),
                )

    def test_ngrok_extracts_url_from_json_and_text(self) -> None:
        provider = NgrokProvider(lambda _message: None)
        self.assertEqual(
            provider._extract_url('{"url":"https://demo.ngrok.app"}'),
            "https://demo.ngrok.app",
        )
        self.assertEqual(
            provider._extract_url("started tunnel https://demo.ngrok.app -> localhost"),
            "https://demo.ngrok.app",
        )


if __name__ == "__main__":
    unittest.main()

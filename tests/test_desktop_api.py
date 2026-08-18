from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_tools_launcher.desktop_api import DesktopAPI


class DesktopAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.settings: dict[str, object] = {}
        self.patches = [
            patch(
                "coding_tools_launcher.server_profiles.settings_dir",
                return_value=self.base,
            ),
            patch(
                "coding_tools_launcher.oauth_persistence.settings_dir",
                return_value=self.base,
            ),
            patch(
                "coding_tools_launcher.desktop_api.load_settings",
                side_effect=lambda: dict(self.settings),
            ),
            patch(
                "coding_tools_launcher.desktop_api.save_settings",
                side_effect=lambda value: self.settings.update(value),
            ),
        ]
        for item in self.patches:
            item.start()
        self.api = DesktopAPI()

    def tearDown(self) -> None:
        self.api._close()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def payload(self, *, name: str = "Server A", port: int = 8234) -> dict[str, object]:
        return {
            "name": name,
            "workspace": str(self.base),
            "oauth_password": "password",
            "host": "127.0.0.1",
            "port": port,
            "remember_secrets": True,
            "network": {
                "provider": "external",
                "public_url": f"https://{name.lower().replace(' ', '-')}.example.com",
                "options": {},
            },
        }

    def test_create_server_returns_serializable_profile(self) -> None:
        created = self.api.create_server(self.payload())
        self.assertEqual(created["name"], "Server A")
        self.assertEqual(created["port"], 8234)
        self.assertEqual(created["lifecycle"], "persistent")
        self.assertFalse(created["running"])
        self.assertTrue(created["server_id"])

    def test_next_port_advances_after_profile_creation(self) -> None:
        self.api.create_server(self.payload())
        self.assertEqual(self.api.get_next_port(), 8235)

    def test_duplicate_port_is_rejected(self) -> None:
        self.api.create_server(self.payload())
        with self.assertRaisesRegex(ValueError, "相同地址"):
            self.api.create_server(self.payload(name="Server B", port=8234))

    def test_secret_persistence_can_be_disabled(self) -> None:
        payload = self.payload()
        payload["remember_secrets"] = False
        payload["network"] = {
            "provider": "cloudflare",
            "public_url": "https://mcp.example.com",
            "options": {"tunnel_token": "secret-token"},
        }
        created = self.api.create_server(payload)
        self.assertEqual(created["oauth_password"], "")
        self.assertNotIn("tunnel_token", created["network"]["options"])

    def test_bootstrap_restores_selected_server(self) -> None:
        first = self.api.create_server(self.payload())
        second = self.api.create_server(self.payload(name="Server B", port=8235))
        self.api.select_server(first["server_id"])
        bootstrap = self.api.bootstrap()
        self.assertEqual(bootstrap["selected_server_id"], first["server_id"])
        self.assertEqual(len(bootstrap["servers"]), 2)
        self.assertNotEqual(first["server_id"], second["server_id"])


if __name__ == "__main__":
    unittest.main()

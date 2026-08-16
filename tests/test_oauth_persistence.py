from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_tools_launcher import oauth_persistence


class OAuthPersistenceTests(unittest.TestCase):
    def test_token_secret_is_stable_for_same_server_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch.object(oauth_persistence, "settings_dir", return_value=base):
                first = oauth_persistence.prepare_oauth_persistence(
                    "https://mcp.example.com"
                )
                second = oauth_persistence.prepare_oauth_persistence(
                    "https://mcp.example.com/"
                )

            self.assertEqual(first.token_secret_hex, second.token_secret_hex)
            self.assertEqual(first.registry_file, second.registry_file)
            self.assertEqual(len(bytes.fromhex(first.token_secret_hex)), 32)

    def test_dynamic_client_survives_new_registry_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry_file = Path(temporary) / "clients.json"
            with patch.dict(
                os.environ,
                {
                    oauth_persistence.OAUTH_REGISTRY_FILE_ENV: str(registry_file),
                },
            ):
                oauth_persistence.install_oauth_registry_persistence()

                from coding_tools_mcp.oauth import OAuthClientRegistry

                first = OAuthClientRegistry()
                registered = first.register(
                    {
                        "redirect_uris": ["https://chat.example.com/oauth/callback"],
                        "grant_types": ["authorization_code"],
                        "response_types": ["code"],
                        "token_endpoint_auth_method": "client_secret_post",
                        "client_name": "restart-test",
                    }
                )

                second = OAuthClientRegistry()
                client_id = registered["client_id"]
                client_secret = registered["client_secret"]

                self.assertIsNotNone(second.get(client_id))
                self.assertTrue(
                    second.authenticates(
                        client_id,
                        client_secret,
                        "client_secret_post",
                    )
                )


if __name__ == "__main__":
    unittest.main()

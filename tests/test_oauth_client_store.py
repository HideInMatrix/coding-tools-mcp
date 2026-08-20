from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_workbench import oauth_persistence
from agent_workbench.oauth_client_store import CIMDClientStore, OAuthClientStore


class OAuthClientStoreTests(unittest.TestCase):
    def _write_registry(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "clients": [
                        {
                            "client_id": "client-a",
                            "redirect_uris": ["https://chat.example.com/a"],
                            "token_endpoint_auth_method": "none",
                            "client_name": "Chat A",
                            "secret_digest": None,
                            "issued_at": 10,
                        },
                        {
                            "client_id": "client-b",
                            "redirect_uris": ["https://chat.example.com/b"],
                            "token_endpoint_auth_method": "none",
                            "client_name": "Chat B",
                            "secret_digest": None,
                            "issued_at": 20,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_list_hides_secret_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "clients.json"
            self._write_registry(path)
            items = OAuthClientStore("server-a", path=path).list()
            self.assertEqual([item.client_id for item in items], ["client-a", "client-b"])
            self.assertFalse(hasattr(items[0], "secret_digest"))

    def test_remove_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "clients.json"
            self._write_registry(path)
            store = OAuthClientStore("server-a", path=path)
            self.assertTrue(store.remove("client-a"))
            self.assertEqual([item.client_id for item in store.list()], ["client-b"])

    def test_clear_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "clients.json"
            self._write_registry(path)
            store = OAuthClientStore("server-a", path=path)
            self.assertEqual(store.clear(), 2)
            self.assertEqual(store.list(), [])

    def test_default_store_follows_server_binding_to_issuer_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch.object(oauth_persistence, "settings_dir", return_value=base):
                persistence = oauth_persistence.prepare_issuer_oauth_persistence(
                    "https://mcp.example.com"
                )
                self._write_registry(persistence.registry_file)
                oauth_persistence.bind_server_oauth_issuer(
                    "server-a",
                    "https://mcp.example.com",
                )
                items = OAuthClientStore("server-a").list()
            self.assertEqual(
                [item.client_id for item in items],
                ["client-a", "client-b"],
            )

    def test_cimd_store_is_read_only_summary_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cimd-clients.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "clients": [
                            {
                                "client_id": "https://chat.example.com/client.json",
                                "client_name": "Chat Example",
                                "redirect_uris": ["https://chat.example.com/callback"],
                                "token_endpoint_auth_method": "none",
                                "observed_at": 30,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            items = CIMDClientStore("server-a", path=path).list()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].client_type, "cimd")
            self.assertFalse(items[0].revocable)
            self.assertEqual(items[0].issued_at, 30)


if __name__ == "__main__":
    unittest.main()

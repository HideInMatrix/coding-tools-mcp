from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coding_tools_launcher.oauth_client_store import OAuthClientStore


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


if __name__ == "__main__":
    unittest.main()

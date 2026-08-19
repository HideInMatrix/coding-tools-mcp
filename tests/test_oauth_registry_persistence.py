from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_launcher.oauth_persistence import install_oauth_registry_persistence
from mcp_tools_server.oauth import OAuthClientRegistry


class OAuthRegistryPersistenceTests(unittest.TestCase):
    def test_registry_persistence_is_instance_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            company_file = root / "company" / "clients.json"
            home_file = root / "home" / "clients.json"
            company = OAuthClientRegistry(company_file)
            home = OAuthClientRegistry(home_file)

            company_result = company.register(
                {"redirect_uris": ["https://example.com/company/callback"]}
            )
            home_result = home.register(
                {"redirect_uris": ["https://example.com/home/callback"]}
            )

            company_reloaded = OAuthClientRegistry(company_file)
            home_reloaded = OAuthClientRegistry(home_file)
            self.assertIsNotNone(company_reloaded.get(company_result["client_id"]))
            self.assertIsNone(company_reloaded.get(home_result["client_id"]))
            self.assertIsNotNone(home_reloaded.get(home_result["client_id"]))
            self.assertIsNone(home_reloaded.get(company_result["client_id"]))

    def test_remove_and_clear_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "clients.json"
            registry = OAuthClientRegistry(path)
            first = registry.register(
                {"redirect_uris": ["https://example.com/first/callback"]}
            )
            registry.register(
                {"redirect_uris": ["https://example.com/second/callback"]}
            )

            self.assertTrue(registry.remove(first["client_id"]))
            reloaded = OAuthClientRegistry(path)
            self.assertIsNone(reloaded.get(first["client_id"]))
            self.assertEqual(len(reloaded.list_clients()), 1)

            self.assertEqual(reloaded.clear(), 1)
            self.assertEqual(OAuthClientRegistry(path).list_clients(), ())

    def test_launcher_monkeypatch_hook_is_a_noop_for_builtin_persistence(self) -> None:
        original_init = OAuthClientRegistry.__init__
        install_oauth_registry_persistence()
        self.assertIs(OAuthClientRegistry.__init__, original_init)


if __name__ == "__main__":
    unittest.main()

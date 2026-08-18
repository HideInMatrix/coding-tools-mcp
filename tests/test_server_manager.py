from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from coding_tools_launcher.config import LaunchInfo
from coding_tools_launcher.server_manager import MCPServerManager
from coding_tools_launcher.server_profiles import ServerProfileStore


class MCPServerManagerTests(unittest.TestCase):
    def test_statuses_include_every_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ServerProfileStore(Path(temporary) / "servers.json")
            first = store.create(
                name="A",
                workspace=Path(temporary) / "a",
                oauth_password="a-password",
            )
            second = store.create(
                name="B",
                workspace=Path(temporary) / "b",
                oauth_password="b-password",
            )
            manager = MCPServerManager(store=store)

            statuses = manager.statuses()

            self.assertEqual([item.server_id for item in statuses], [first.server_id, second.server_id])
            self.assertTrue(all(not item.running for item in statuses))

    def test_start_uses_independent_launcher_per_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "a").mkdir()
            (base / "b").mkdir()
            store = ServerProfileStore(base / "servers.json")
            first = store.create(
                name="A",
                workspace=base / "a",
                oauth_password="a-password",
            )
            second = store.create(
                name="B",
                workspace=base / "b",
                oauth_password="b-password",
            )
            manager = MCPServerManager(store=store)
            launchers: list[MagicMock] = []

            def fake_launcher(_log):
                launcher = MagicMock()
                launcher.is_running = False
                launcher.info = None
                launcher.exit_reason = ""
                launcher.start.side_effect = lambda config: LaunchInfo(
                    workspace=config.workspace,
                    local_mcp_url=f"http://{config.host}:{config.port}/mcp",
                    tunnel_url="https://example.com",
                    public_base_url="https://example.com",
                    public_mcp_url="https://example.com/mcp",
                    url_mode="test",
                )
                launchers.append(launcher)
                return launcher

            with patch("coding_tools_launcher.server_manager.MCPLauncher", side_effect=fake_launcher):
                manager.start(first.server_id)
                manager.start(second.server_id)

            self.assertEqual(len(launchers), 2)
            first_config = launchers[0].start.call_args.args[0]
            second_config = launchers[1].start.call_args.args[0]
            self.assertEqual(first_config.server_id, first.server_id)
            self.assertEqual(second_config.server_id, second.server_id)
            self.assertEqual(first_config.port, 8234)
            self.assertEqual(second_config.port, 8235)

    def test_delete_running_profile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ServerProfileStore(Path(temporary) / "servers.json")
            profile = store.create(
                name="A",
                workspace=Path(temporary) / "a",
                oauth_password="password",
            )
            manager = MCPServerManager(store=store)
            launcher = MagicMock()
            launcher.is_running = True
            manager._launchers[profile.server_id] = launcher

            with self.assertRaisesRegex(RuntimeError, "先停止"):
                manager.delete_profile(profile.server_id)

    def test_delete_profile_removes_persistent_oauth_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            store = ServerProfileStore(base / "servers.json")
            profile = store.create(
                name="A",
                workspace=base / "a",
                oauth_password="password",
            )
            manager = MCPServerManager(store=store)

            with patch(
                "coding_tools_launcher.server_manager.delete_server_oauth_storage"
            ) as delete_oauth:
                self.assertTrue(manager.delete_profile(profile.server_id))

            delete_oauth.assert_called_once_with(profile.server_id)
            self.assertIsNone(store.get(profile.server_id))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import threading
import time
import unittest
from unittest.mock import patch

from agent_workbench.permission_broker import DesktopPermissionBroker
from agent_runtime.local_permission_broker import LocalPermissionBrokerClient


class PermissionBrokerTests(unittest.TestCase):
    def test_signed_request_can_be_approved_and_sensitive_fields_are_redacted(self) -> None:
        broker = DesktopPermissionBroker()
        try:
            env = broker.child_environment("server-a")
            with patch.dict(os.environ, env, clear=False):
                client = LocalPermissionBrokerClient.from_env()
            self.assertIsNotNone(client)
            assert client is not None
            result: dict[str, str] = {}

            def request_permission() -> None:
                decision = client.request(
                    tool_name="exec_command",
                    arguments={
                        "cmd": "git commit -m test",
                        "env": {"API_TOKEN": "secret-value", "MODE": "test"},
                    },
                    permission="git_metadata_write",
                    reason="Commit the requested changes",
                    principal="principal-a",
                    timeout_seconds=5,
                )
                result["status"] = decision.status
                result["scope"] = decision.scope

            thread = threading.Thread(target=request_permission)
            thread.start()
            pending: list[dict[str, object]] = []
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                pending = broker.pending()
                if pending:
                    break
                time.sleep(0.05)

            self.assertEqual(len(pending), 1)
            request = pending[0]
            self.assertEqual(request["server_id"], "server-a")
            self.assertEqual(request["permission"], "git_metadata_write")
            arguments = request["arguments"]
            self.assertIsInstance(arguments, dict)
            assert isinstance(arguments, dict)
            env_preview = arguments["env"]
            self.assertIsInstance(env_preview, dict)
            assert isinstance(env_preview, dict)
            self.assertEqual(env_preview["API_TOKEN"], "<redacted>")
            self.assertEqual(env_preview["MODE"], "test")
            self.assertTrue(broker.respond(str(request["request_id"]), True))
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result["status"], "approved")
            self.assertEqual(result["scope"], "once")
        finally:
            broker.cleanup()

    def test_signed_request_can_approve_all_permissions_for_server_session(self) -> None:
        broker = DesktopPermissionBroker()
        try:
            env = broker.child_environment("server-a")
            with patch.dict(os.environ, env, clear=False):
                client = LocalPermissionBrokerClient.from_env()
            self.assertIsNotNone(client)
            assert client is not None
            result: dict[str, str] = {}

            def request_permission() -> None:
                decision = client.request(
                    tool_name="exec_process",
                    arguments={"program": "pnpm", "args": ["build"]},
                    permission="privileged_executable",
                    reason="Need the user tool environment",
                    principal="principal-a",
                    timeout_seconds=5,
                )
                result["status"] = decision.status
                result["scope"] = decision.scope

            thread = threading.Thread(target=request_permission)
            thread.start()
            pending: list[dict[str, object]] = []
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                pending = broker.pending()
                if pending:
                    break
                time.sleep(0.05)

            self.assertEqual(len(pending), 1)
            request_id = str(pending[0]["request_id"])
            self.assertTrue(broker.respond(request_id, "session"))
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result["status"], "approved")
            self.assertEqual(result["scope"], "session")
        finally:
            broker.cleanup()


if __name__ == "__main__":
    unittest.main()

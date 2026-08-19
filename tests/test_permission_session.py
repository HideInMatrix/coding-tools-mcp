from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcp_tools_server.errors import RpcError
from mcp_tools_server.permissions import PermissionSession
from mcp_tools_server.protocol import RequestContext


def modern_context(
    *,
    principal: str = "user-a",
    request_state: str | None = None,
    input_responses: dict[str, object] | None = None,
) -> RequestContext:
    return RequestContext(
        era="modern",
        protocol_version="2026-07-28",
        client_capabilities={"elicitation": {"form": {}}},
        input_responses=input_responses,
        request_state=request_state,
        principal=principal,
    )


class PermissionSessionTests(unittest.TestCase):
    def test_request_state_is_bound_and_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = PermissionSession(Path(temporary))
            required = session.input_required(
                name="exec_command",
                arguments={"cmd": "git commit"},
                permission="git_metadata_write",
                message="permission required",
                context=modern_context(),
                granted=frozenset(),
            )
            self.assertIsNotNone(required)
            assert required is not None
            state = str(required["requestState"])
            accepted = modern_context(
                request_state=state,
                input_responses={
                    "permission": {
                        "action": "accept",
                        "content": {"confirm": True},
                    }
                },
            )

            granted, denied = session.permission_round(
                "exec_command",
                {"cmd": "git commit"},
                accepted,
            )
            self.assertFalse(denied)
            self.assertIn("git_metadata_write", granted)
            with self.assertRaises(RpcError):
                session.permission_round(
                    "exec_command",
                    {"cmd": "git commit"},
                    accepted,
                )

    def test_request_state_rejects_different_principal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = PermissionSession(Path(temporary))
            required = session.input_required(
                name="exec_command",
                arguments={"cmd": "git commit"},
                permission="git_metadata_write",
                message="permission required",
                context=modern_context(principal="user-a"),
                granted=frozenset(),
            )
            assert required is not None

            with self.assertRaises(RpcError):
                session.permission_round(
                    "exec_command",
                    {"cmd": "git commit"},
                    modern_context(
                        principal="user-b",
                        request_state=str(required["requestState"]),
                        input_responses={
                            "permission": {
                                "action": "accept",
                                "content": {"confirm": True},
                            }
                        },
                    ),
                )

    def test_once_grant_is_argument_bound_and_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = PermissionSession(Path(temporary))
            session.store_grant(
                tool_name="exec_command",
                arguments={"cmd": "git commit"},
                permission="git_metadata_write",
                principal="user-a",
                scope="once",
                ttl_seconds=300,
            )
            context = modern_context(principal="user-a")

            self.assertEqual(
                session.stored_permissions_for_call(
                    "exec_command",
                    {"cmd": "git status"},
                    context,
                ),
                frozenset(),
            )
            self.assertEqual(
                session.stored_permissions_for_call(
                    "exec_command",
                    {"cmd": "git commit"},
                    context,
                ),
                frozenset({"git_metadata_write"}),
            )
            self.assertEqual(
                session.stored_permissions_for_call(
                    "exec_command",
                    {"cmd": "git commit"},
                    context,
                ),
                frozenset(),
            )

    def test_session_grant_is_principal_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = PermissionSession(Path(temporary))
            session.grant_session_permissions(modern_context(principal="user-a"))

            self.assertTrue(
                session.session_permissions_for_call(
                    modern_context(principal="user-a")
                )
            )
            self.assertEqual(
                session.session_permissions_for_call(
                    modern_context(principal="user-b")
                ),
                frozenset(),
            )


if __name__ == "__main__":
    unittest.main()

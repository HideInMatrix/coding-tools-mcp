from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_workbench.permission_broker import DesktopPermissionBroker
from agent_runtime.local_permission_broker import LocalWorkflowApprovalBrokerClient
from agent_runtime.runtime import Runtime
from tests.workbench_fixtures import install_project_development_fixture


class WorkflowApprovalBrokerTests(unittest.TestCase):
    def test_signed_request_and_response_round_trip(self) -> None:
        broker = DesktopPermissionBroker()
        try:
            client = LocalWorkflowApprovalBrokerClient.from_values(
                directory=broker.directory,
                secret_hex=broker.secret.hex(),
                server_id="server-a",
            )
            request_id = client.publish(
                run_id="a" * 24,
                node_id="approval.node",
                approval_id="approval_1",
                title="确认设计",
                description="继续后执行代码修改",
            )

            pending = broker.pending_workflow_approvals()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["request_id"], request_id)
            self.assertEqual(pending[0]["server_id"], "server-a")
            self.assertEqual(pending[0]["run_id"], "a" * 24)

            self.assertTrue(broker.respond_workflow_approval(request_id, True))
            self.assertTrue(
                client.consume_response(
                    request_id,
                    run_id="a" * 24,
                    node_id="approval.node",
                    approval_id="approval_1",
                )
            )
            self.assertEqual(broker.pending_workflow_approvals(), [])
        finally:
            broker.cleanup()

    def test_unsigned_or_mismatched_response_cannot_approve(self) -> None:
        broker = DesktopPermissionBroker()
        try:
            client = LocalWorkflowApprovalBrokerClient.from_values(
                directory=broker.directory,
                secret_hex=broker.secret.hex(),
                server_id="server-a",
            )
            request_id = client.publish(
                run_id="b" * 24,
                node_id="approval",
                approval_id="approval_2",
                title="Approve",
                description="",
            )
            forged = broker.directory / f"{request_id}.workflow-approval.response.json"
            forged.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "kind": "workflow_approval",
                        "request_id": request_id,
                        "server_id": "server-a",
                        "run_id": "b" * 24,
                        "node_id": "approval",
                        "approval_id": "approval_2",
                        "approved": True,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "签名"):
                client.consume_response(
                    request_id,
                    run_id="b" * 24,
                    node_id="approval",
                    approval_id="approval_2",
                )
        finally:
            broker.cleanup()

    def test_workflow_continue_consumes_desktop_decision_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            broker = DesktopPermissionBroker()
            environment = broker.child_environment("server-company")
            try:
                with patch.dict(os.environ, environment, clear=False):
                    runtime = Runtime(workspace)
                    try:
                        install_project_development_fixture(runtime)
                        started = runtime.call_tool(
                            "workflow_start",
                            {"workflow_id": "project-development"},
                        )["structuredContent"]["run"]
                        continued = runtime.call_tool(
                            "workflow_continue",
                            {
                                "run_id": started["run_id"],
                                "node_id": str(started["pending_action"]["node_id"]),
                                "outcome": "success",
                                "output": {"summary": "baseline"},
                            },
                        )["structuredContent"]["run"]
                        self.assertEqual(continued["status"], "waiting_approval")
                        pending = broker.pending_workflow_approvals()
                        self.assertEqual(len(pending), 1)
                        request_id = pending[0]["request_id"]

                        forged_attempt = runtime.call_tool(
                            "workflow_continue",
                            {
                                "run_id": started["run_id"],
                                "node_id": "approval",
                                "outcome": "success",
                                "output": {"approved": True},
                            },
                        )["structuredContent"]
                        self.assertFalse(forged_attempt["ok"])
                        self.assertEqual(
                            forged_attempt["error"]["code"],
                            "WORKFLOW_CONTINUE_FAILED",
                        )

                        pending_attempt = runtime.call_tool(
                            "workflow_continue",
                            {"run_id": started["run_id"]},
                        )["structuredContent"]
                        self.assertFalse(pending_attempt["ok"])
                        self.assertIn("still pending", pending_attempt["error"]["message"])

                        self.assertTrue(
                            broker.respond_workflow_approval(request_id, True)
                        )
                        finished = runtime.call_tool(
                            "workflow_continue",
                            {"run_id": started["run_id"]},
                        )["structuredContent"]["run"]
                        self.assertEqual(finished["status"], "succeeded")
                    finally:
                        runtime.close()
            finally:
                broker.cleanup()

    def test_pending_approval_is_republished_after_desktop_broker_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            first_broker = DesktopPermissionBroker()
            try:
                with patch.dict(
                    os.environ,
                    first_broker.child_environment("server-company"),
                    clear=False,
                ):
                    runtime = Runtime(workspace)
                    try:
                        install_project_development_fixture(runtime)
                        run = runtime.workflow_runs.start("project-development")
                        run = runtime.workflow_runs.continue_model(
                            run.run_id,
                            node_id=str(run.pending_action["node_id"]),
                            outcome="success",
                            output={"ok": True},
                        )
                        self.assertEqual(run.status, "waiting_approval")
                        self.assertEqual(len(first_broker.pending_workflow_approvals()), 1)
                    finally:
                        runtime.close()
            finally:
                first_broker.cleanup()

            second_broker = DesktopPermissionBroker()
            try:
                with patch.dict(
                    os.environ,
                    second_broker.child_environment("server-company"),
                    clear=False,
                ):
                    restarted = Runtime(workspace)
                    try:
                        pending = second_broker.pending_workflow_approvals()
                        self.assertEqual(len(pending), 1)
                        self.assertEqual(pending[0]["run_id"], run.run_id)
                    finally:
                        restarted.close()
            finally:
                second_broker.cleanup()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.protocol import RequestContext
from agent_runtime.runtime import Runtime
from agent_runtime.workbench import WorkflowDefinition
from tests.workbench_fixtures import install_project_development_fixture


def tool_then_approval_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.from_mapping(
        {
            "schema_version": 1,
            "id": "tool-approval",
            "name": "Tool Approval",
            "description": "Tool execution followed by approval",
            "version": 1,
            "entry_node_id": "info",
            "nodes": [
                {
                    "id": "info",
                    "type": "tool",
                    "name": "Info",
                    "config": {
                        "provider": "system",
                        "tool_name": "server_info",
                        "arguments": {},
                    },
                },
                {
                    "id": "approval",
                    "type": "approval",
                    "name": "Approval",
                    "config": {"title": "Continue"},
                },
            ],
            "edges": [
                {"id": "info-approval", "source": "info", "target": "approval"}
            ],
        }
    )


def artifact_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.from_mapping(
        {
            "schema_version": 1,
            "id": "artifact-flow",
            "name": "Artifact Flow",
            "description": "Persist a tool result as an artifact before approval",
            "version": 1,
            "entry_node_id": "info",
            "nodes": [
                {
                    "id": "info",
                    "type": "tool",
                    "name": "Info",
                    "config": {
                        "provider": "system",
                        "tool_name": "server_info",
                        "arguments": {},
                    },
                },
                {
                    "id": "snapshot",
                    "type": "artifact",
                    "name": "Snapshot",
                    "config": {
                        "artifact_id": "server-info",
                        "source_node_id": "info",
                        "format": "json",
                    },
                },
                {
                    "id": "approval",
                    "type": "approval",
                    "name": "Approval",
                    "config": {"title": "Accept artifact"},
                },
            ],
            "edges": [
                {"id": "info-snapshot", "source": "info", "target": "snapshot"},
                {"id": "snapshot-approval", "source": "snapshot", "target": "approval"},
            ],
        }
    )


class WorkflowRunTests(unittest.TestCase):
    def test_all_builtin_engineering_workflows_complete_example_lifecycle(self) -> None:
        inputs = {
            "project-development": {"feature": "visual workflow editor"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                install_project_development_fixture(runtime)
                for workflow_id, workflow_inputs in inputs.items():
                    with self.subTest(workflow=workflow_id):
                        run = runtime.workflow_runs.start(
                            workflow_id,
                            inputs=workflow_inputs,
                        )
                        self.assertEqual(run.status, "waiting_model")
                        node_id = str(run.pending_action["node_id"])
                        run = runtime.workflow_runs.continue_model(
                            run.run_id,
                            node_id=node_id,
                            outcome="success",
                            output={
                                "workflow": workflow_id,
                                "summary": "validated example result",
                            },
                        )
                        self.assertEqual(run.status, "waiting_approval")
                        self.assertEqual(len(run.artifacts), 1)
                        run = runtime.workflow_runs.resolve_approval(
                            run.run_id,
                            approved=True,
                        )
                        self.assertEqual(run.status, "succeeded")
            finally:
                runtime.close()
    def test_model_action_continue_and_human_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                install_project_development_fixture(runtime)
                run = runtime.workflow_runs.start("project-development")
                self.assertEqual(run.status, "waiting_model")
                model_node_id = str(run.pending_action["node_id"])

                run = runtime.workflow_runs.continue_model(
                    run.run_id,
                    node_id=model_node_id,
                    outcome="success",
                    output={"summary": "baseline"},
                )
                self.assertEqual(run.status, "waiting_approval")
                self.assertEqual(run.pending_action["type"], "approval")

                with self.assertRaisesRegex(ValueError, "not waiting for a model"):
                    runtime.workflow_runs.continue_model(
                        run.run_id,
                        node_id="approval",
                        outcome="success",
                        output={},
                    )

                run = runtime.workflow_runs.resolve_approval(run.run_id, approved=True)
                self.assertEqual(run.status, "succeeded")
            finally:
                runtime.close()

    def test_run_snapshot_survives_workflow_override_and_runtime_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runtime = Runtime(workspace)
            try:
                install_project_development_fixture(runtime)
                run = runtime.workflow_runs.start("project-development")
                old_name = run.workflow_snapshot["name"]

                replacement = WorkflowDefinition.from_mapping(
                    {
                        "schema_version": 1,
                        "id": "project-development",
                        "name": "Replacement",
                        "description": "Replacement workflow fixture",
                        "version": 1,
                        "entry_node_id": "new-entry",
                        "nodes": [
                            {
                                "id": "new-entry",
                                "type": "approval",
                                "name": "New",
                                "config": {"title": "New"},
                            }
                        ],
                        "edges": [],
                    }
                )
                saved = runtime.workflow_store.save(replacement)
                runtime.workflow_registry.register(saved, replace=True)
                self.assertEqual(runtime.workflow_registry.get("project-development").name, "Replacement")
            finally:
                runtime.close()

            restarted = Runtime(workspace)
            try:
                persisted = restarted.workflow_runs.get(run.run_id)
                assert persisted is not None
                self.assertEqual(persisted.workflow_snapshot["name"], old_name)
                continued = restarted.workflow_runs.continue_model(
                    run.run_id,
                    node_id=str(run.pending_action["node_id"]),
                    outcome="success",
                    output={"ok": True},
                )
                self.assertEqual(continued.status, "waiting_approval")
                self.assertEqual(continued.pending_action["node_id"], "approval")
            finally:
                restarted.close()

    def test_artifact_is_written_inside_run_and_approval_can_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runtime = Runtime(workspace)
            try:
                saved = runtime.workflow_store.save(artifact_workflow())
                runtime.workflow_registry.register(saved, replace=True)
                run = runtime.workflow_runs.start("artifact-flow")
                self.assertEqual(run.status, "waiting_approval")
                self.assertEqual(len(run.artifacts), 1)
                artifact = run.artifacts[0]
                artifact_path = workspace / artifact.path
                self.assertTrue(artifact_path.is_file())
                self.assertTrue(str(artifact_path).startswith(str(workspace)))
                run = runtime.workflow_runs.resolve_approval(run.run_id, approved=True)
                self.assertEqual(run.status, "succeeded")
            finally:
                runtime.close()

    def test_failed_tool_run_can_retry_from_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runtime = Runtime(workspace)
            try:
                workflow = WorkflowDefinition.from_mapping(
                    {
                        "schema_version": 1,
                        "id": "retry-flow",
                        "name": "Retry",
                        "description": "Retry a failed file read after the input appears",
                        "version": 1,
                        "entry_node_id": "read",
                        "nodes": [
                            {
                                "id": "read",
                                "type": "tool",
                                "name": "Read",
                                "config": {
                                    "provider": "system",
                                    "tool_name": "read_file",
                                    "arguments": {"path": "later.txt"},
                                },
                            }
                        ],
                        "edges": [],
                    }
                )
                saved = runtime.workflow_store.save(workflow)
                runtime.workflow_registry.register(saved, replace=True)
                run = runtime.workflow_runs.start("retry-flow")
                self.assertEqual(run.status, "failed")
                self.assertIsNotNone(run.retry_state)

                (workspace / "later.txt").write_text("ready", encoding="utf-8")
                run = runtime.workflow_runs.retry(run.run_id)
                self.assertEqual(run.status, "succeeded")
            finally:
                runtime.close()

    def test_workflow_start_tool_inherits_outer_request_principal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                saved = runtime.workflow_store.save(tool_then_approval_workflow())
                runtime.workflow_registry.register(saved, replace=True)
                original = runtime.permission_session.permission_round
                seen: list[tuple[str, str]] = []

                def recording(name, arguments, context):
                    seen.append((name, context.principal if context else ""))
                    return original(name, arguments, context)

                context = RequestContext(
                    "legacy",
                    "2025-11-25",
                    principal="oauth-client:test-client",
                )
                with patch.object(
                    runtime.permission_session,
                    "permission_round",
                    side_effect=recording,
                ):
                    result = runtime.call_tool(
                        "workflow_start",
                        {"workflow_id": "tool-approval"},
                        context=context,
                    )
                self.assertIn("structuredContent", result)
            finally:
                runtime.close()

        by_tool = {name: principal for name, principal in seen}
        self.assertEqual(by_tool["workflow_start"], "oauth-client:test-client")
        self.assertEqual(by_tool["server_info"], "oauth-client:test-client")

    def test_spec_workflow_waits_on_skill_model_action_and_preserves_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                install_project_development_fixture(runtime)
                result = runtime.call_tool(
                    "workflow_start",
                    {
                        "workflow_id": "project-development",
                        "inputs": {"feature": "visual workflow editor"},
                    },
                )
                run = result["structuredContent"]["run"]
            finally:
                runtime.close()

        self.assertEqual(run["status"], "waiting_model")
        self.assertEqual(run["inputs"], {"feature": "visual workflow editor"})
        self.assertEqual(run["pending_action"]["node_type"], "skill")
        self.assertIn("method_document", run["pending_action"]["skill"])
        self.assertNotIn("prompt_id", run["pending_action"])
        self.assertNotIn("allowed_tools", run["pending_action"])

    def test_cancel_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runtime = Runtime(workspace)
            try:
                install_project_development_fixture(runtime)
                run = runtime.workflow_runs.start("project-development")
                cancelled = runtime.workflow_runs.cancel(run.run_id)
                self.assertEqual(cancelled.status, "cancelled")
            finally:
                runtime.close()
            restarted = Runtime(workspace)
            try:
                persisted = restarted.workflow_runs.get(run.run_id)
                assert persisted is not None
                self.assertEqual(persisted.status, "cancelled")
            finally:
                restarted.close()


if __name__ == "__main__":
    unittest.main()

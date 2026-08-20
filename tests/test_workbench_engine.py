from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime.runtime import Runtime
from agent_runtime.workbench import (
    WorkflowDefinition,
    WorkflowEngine,
    evaluate_condition,
)


def workflow_with_nodes(nodes: list[dict[str, object]], edges: list[dict[str, object]]) -> WorkflowDefinition:
    return WorkflowDefinition.from_mapping(
        {
            "schema_version": 1,
            "id": "engine-test",
            "name": "Engine Test",
            "description": "Engine behavior fixture",
            "version": 1,
            "entry_node_id": str(nodes[0]["id"]),
            "nodes": nodes,
            "edges": edges,
        }
    )


class ConditionLanguageTests(unittest.TestCase):
    def test_restricted_condition_language(self) -> None:
        values = {"result": {"ok": True, "kind": "ready"}}
        self.assertTrue(evaluate_condition("true", values))
        self.assertFalse(evaluate_condition("false", values))
        self.assertTrue(evaluate_condition("result.ok", values))
        self.assertFalse(evaluate_condition("!result.ok", values))
        self.assertTrue(evaluate_condition('result.kind == "ready"', values))
        self.assertTrue(evaluate_condition('result.kind != "failed"', values))
        with self.assertRaisesRegex(ValueError, "unsupported condition"):
            evaluate_condition("__import__('os').system('x')", values)


class WorkflowEngineTests(unittest.TestCase):
    def test_scheduler_activates_matching_edge_in_definition_order(self) -> None:
        workflow = workflow_with_nodes(
            [
                {"id": "a", "type": "approval", "name": "A", "config": {"title": "A"}},
                {"id": "b", "type": "approval", "name": "B", "config": {"title": "B"}},
                {"id": "c", "type": "approval", "name": "C", "config": {"title": "C"}},
            ],
            [
                {"id": "a-c", "source": "a", "target": "c", "condition": "approved"},
                {"id": "a-b", "source": "a", "target": "b", "condition": "approved"},
            ],
        )
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                engine = WorkflowEngine(runtime)
                state = engine.start(workflow)
                next_state = engine.complete(workflow, state, "a", outcome="approved")
            finally:
                runtime.close()

        self.assertEqual(state.ready, ("a",))
        self.assertEqual(next_state.ready, ("b", "c"))
        self.assertEqual(next_state.completed, ("a",))

    def test_prompt_node_produces_model_action_without_calling_model(self) -> None:
        workflow = workflow_with_nodes(
            [
                {
                    "id": "prompt",
                    "type": "prompt",
                    "name": "Prompt",
                    "config": {
                        "prompt_id": "project-analysis",
                        "arguments": {"goal": "map runtime"},
                    },
                }
            ],
            [],
        )
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                engine = WorkflowEngine(runtime)
                state = engine.start(workflow)
                action = engine.model_action(workflow, state, "prompt")
            finally:
                runtime.close()

        self.assertEqual(action.node_type, "prompt")
        self.assertIn("map runtime", action.messages[0]["content"]["text"])
        self.assertEqual(action.allowed_tools, ())

    def test_skill_node_exposes_only_effective_allowlist(self) -> None:
        workflow = workflow_with_nodes(
            [
                {
                    "id": "review",
                    "type": "skill",
                    "name": "Review",
                    "config": {
                        "skill_id": "code-review",
                        "arguments": {"target": "runtime.py"},
                    },
                }
            ],
            [],
        )
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                engine = WorkflowEngine(runtime)
                action = engine.model_action(workflow, engine.start(workflow), "review")
                runtime_tools = {item.name for item in runtime._tools}
            finally:
                runtime.close()

        self.assertEqual(action.node_type, "skill")
        self.assertIsNotNone(action.skill)
        self.assertEqual(set(action.allowed_tools), {"read_file", "search_text", "git_diff", "git_show"} & runtime_tools)
        self.assertNotIn("exec_command", action.allowed_tools)

    def test_tool_node_reuses_runtime_call_tool_and_advances(self) -> None:
        workflow = workflow_with_nodes(
            [
                {
                    "id": "info",
                    "type": "tool",
                    "name": "Info",
                    "config": {"provider": "system", "tool_name": "server_info", "arguments": {}},
                },
                {
                    "id": "done",
                    "type": "approval",
                    "name": "Done",
                    "config": {"title": "Done"},
                },
            ],
            [{"id": "info-done", "source": "info", "target": "done", "condition": "success"}],
        )
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                engine = WorkflowEngine(runtime)
                result = engine.execute_local(workflow, engine.start(workflow), "info")
            finally:
                runtime.close()

        self.assertEqual(result.outcome, "success")
        self.assertEqual(result.state.ready, ("done",))
        self.assertIn("structuredContent", result.output)

    def test_condition_node_routes_true_and_false_without_eval(self) -> None:
        workflow = workflow_with_nodes(
            [
                {
                    "id": "condition",
                    "type": "condition",
                    "name": "Condition",
                    "config": {"expression": "input.ok"},
                },
                {"id": "yes", "type": "approval", "name": "Yes", "config": {"title": "Yes"}},
                {"id": "no", "type": "approval", "name": "No", "config": {"title": "No"}},
            ],
            [
                {"id": "yes-edge", "source": "condition", "target": "yes", "condition": "true"},
                {"id": "no-edge", "source": "condition", "target": "no", "condition": "false"},
            ],
        )
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                engine = WorkflowEngine(runtime)
                result = engine.execute_local(
                    workflow,
                    engine.start(workflow),
                    "condition",
                    values={"input": {"ok": True}},
                )
            finally:
                runtime.close()

        self.assertEqual(result.outcome, "true")
        self.assertEqual(result.state.ready, ("yes",))


if __name__ == "__main__":
    unittest.main()

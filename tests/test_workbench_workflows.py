from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime.workbench import (
    ResourceScope,
    WorkflowDefinition,
    WorkflowStore,
    build_workflow_registry,
    validate_workflow,
)


def workflow_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "reverse-engineering",
        "name": "Reverse Engineering",
        "description": "Inspect the project and pause for review.",
        "version": 1,
        "entry_node_id": "inspect",
        "inputs_schema": {"type": "object", "additionalProperties": True},
        "tags": ["analysis"],
        "nodes": [
            {
                "id": "inspect",
                "type": "skill",
                "name": "Inspect",
                "position": {"x": 100, "y": 100},
                "config": {"skill_id": "project-analysis"},
            },
            {
                "id": "review",
                "type": "approval",
                "name": "Review",
                "position": {"x": 360, "y": 100},
                "config": {},
            },
        ],
        "edges": [
            {
                "id": "inspect-review",
                "source": "inspect",
                "target": "review",
                "condition": "success",
            }
        ],
    }


class WorkflowSchemaTests(unittest.TestCase):
    def test_valid_dag_round_trips(self) -> None:
        workflow = WorkflowDefinition.from_mapping(workflow_payload())
        result = validate_workflow(workflow, skill_ids={"project-analysis"})

        self.assertTrue(result.ok)
        self.assertEqual(workflow.to_dict()["entry_node_id"], "inspect")
        self.assertEqual(workflow.summary()["tags"], ["analysis"])

    def test_description_is_required_by_the_domain_model(self) -> None:
        payload = workflow_payload()
        payload["description"] = ""
        with self.assertRaisesRegex(ValueError, "description must not be empty"):
            WorkflowDefinition.from_mapping(payload)

    def test_cycle_is_rejected(self) -> None:
        payload = workflow_payload()
        payload["edges"] = [
            {"id": "a", "source": "inspect", "target": "review"},
            {"id": "b", "source": "review", "target": "inspect"},
        ]
        workflow = WorkflowDefinition.from_mapping(payload)
        result = validate_workflow(workflow)

        self.assertFalse(result.ok)
        self.assertIn("cycle_detected", {item.code for item in result.errors})

    def test_prompt_node_is_rejected_by_schema(self) -> None:
        payload = workflow_payload()
        payload["nodes"][0]["type"] = "prompt"
        payload["nodes"][0]["config"] = {"prompt_id": "project-analysis"}
        with self.assertRaisesRegex(ValueError, "unsupported workflow node type"):
            WorkflowDefinition.from_mapping(payload)

    def test_unreachable_node_is_rejected(self) -> None:
        payload = workflow_payload()
        payload["nodes"].append(
            {
                "id": "orphan",
                "type": "artifact",
                "name": "Orphan",
                "position": {"x": 100, "y": 300},
                "config": {},
            }
        )
        workflow = WorkflowDefinition.from_mapping(payload)
        result = validate_workflow(workflow, skill_ids={"project-analysis"})

        self.assertFalse(result.ok)
        self.assertIn("unreachable_node", {item.code for item in result.errors})

    def test_unknown_skill_and_tool_are_rejected(self) -> None:
        payload = workflow_payload()
        payload["nodes"] = [
            {
                "id": "skill",
                "type": "skill",
                "name": "Skill",
                "config": {"skill_id": "missing-skill"},
            },
            {
                "id": "tool",
                "type": "tool",
                "name": "Tool",
                "config": {"provider": "system", "tool_name": "missing-tool"},
            },
        ]
        payload["entry_node_id"] = "skill"
        payload["edges"] = [{"id": "skill-tool", "source": "skill", "target": "tool"}]
        workflow = WorkflowDefinition.from_mapping(payload)
        result = validate_workflow(
            workflow,
            skill_ids={"known-skill"},
            tool_names={"known-tool"},
        )

        self.assertFalse(result.ok)
        codes = {item.code for item in result.errors}
        self.assertIn("unknown_skill", codes)
        self.assertIn("unknown_tool", codes)

    def test_condition_requires_expression(self) -> None:
        payload = workflow_payload()
        payload["nodes"] = [
            {
                "id": "condition",
                "type": "condition",
                "name": "Condition",
                "config": {},
            }
        ]
        payload["entry_node_id"] = "condition"
        payload["edges"] = []
        result = validate_workflow(WorkflowDefinition.from_mapping(payload))
        self.assertFalse(result.ok)
        self.assertIn("invalid_condition", {item.code for item in result.errors})

    def test_artifact_config_and_recursive_workflow_tool_are_validated(self) -> None:
        payload = workflow_payload()
        payload["nodes"] = [
            {
                "id": "source",
                "type": "tool",
                "name": "Source",
                "config": {
                    "provider": "system",
                    "tool_name": "read_file",
                    "arguments": {"path": "README.md"},
                },
            },
            {
                "id": "artifact",
                "type": "artifact",
                "name": "Artifact",
                "config": {
                    "artifact_id": "report",
                    "source_node_id": "source",
                    "format": "xml",
                },
            },
            {
                "id": "recursive",
                "type": "tool",
                "name": "Recursive",
                "config": {
                    "provider": "system",
                    "tool_name": "workflow_start",
                    "arguments": {},
                },
            },
        ]
        payload["entry_node_id"] = "source"
        payload["edges"] = [
            {"id": "source-artifact", "source": "source", "target": "artifact"},
            {"id": "artifact-recursive", "source": "artifact", "target": "recursive"},
        ]
        result = validate_workflow(
            WorkflowDefinition.from_mapping(payload),
            tool_names={"read_file", "workflow_start"},
        )
        codes = {item.code for item in result.errors}
        self.assertIn("invalid_artifact_format", codes)
        self.assertIn("recursive_workflow_tool", codes)


class WorkflowRegistryTests(unittest.TestCase):
    def test_registry_has_no_default_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            store = WorkflowStore(workspace)
            registry = build_workflow_registry(store=store)

        self.assertEqual((), registry.list())

    def test_workspace_workflow_delete_does_not_reveal_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            store = WorkflowStore(workspace)
            registry = build_workflow_registry(store=store)
            raw = workflow_payload()
            raw["id"] = "project-development"
            raw["name"] = "Workspace Project Flow"
            saved = store.save(WorkflowDefinition.from_mapping(raw))
            registry.register(saved, replace=True)
            selected = registry.get("project-development")
            assert selected is not None
            self.assertEqual(selected.scope, ResourceScope.WORKSPACE)
            self.assertEqual(selected.name, "Workspace Project Flow")

            self.assertTrue(store.delete("project-development"))
            registry.remove("project-development", scope=ResourceScope.WORKSPACE)
            self.assertIsNone(registry.get("project-development"))

    def test_store_versions_and_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkflowStore(Path(temporary))
            workflow = WorkflowDefinition.from_mapping(workflow_payload())
            first = store.save(workflow)
            second = store.save(workflow)
            self.assertEqual(first.version, 1)
            self.assertEqual(second.version, 2)
            with self.assertRaisesRegex(RuntimeError, "version conflict"):
                store.save(workflow, expected_version=1)
            with self.assertRaisesRegex(ValueError, "invalid workflow id"):
                store.get("../outside")

if __name__ == "__main__":
    unittest.main()

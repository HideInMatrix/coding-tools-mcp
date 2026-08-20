from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_runtime.workbench import (
    CapabilityAssetService,
    ResourceScope,
    SkillVersionConflictError,
    WorkflowDefinition,
    validate_workflow,
)


def skill_payload(skill_id: str = "global-skill") -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": skill_id,
        "name": "Global Skill",
        "description": "Global managed skill",
        "artifacts": ["report.md"],
        "method_document": "# Global Skill\n\n1. Read evidence.\n2. Produce report.",
    }


class CapabilityAssetDomainTests(unittest.TestCase):
    def test_skill_store_crud_versions_without_prompt_or_tool_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            global_root = Path(temporary) / "global-assets"
            service = CapabilityAssetService(global_root=global_root)

            first = service.save_skill(skill_payload(), expected_version=0)
            self.assertEqual(first.version, 1)
            self.assertEqual(first.scope, ResourceScope.GLOBAL)

            metadata = json.loads(
                (global_root / "skills" / "global-skill" / "skill.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("entry_prompt", metadata)
            self.assertNotIn("tool_references", metadata)

            updated = skill_payload()
            updated["name"] = "Updated Skill"
            second = service.save_skill(updated, expected_version=1)
            self.assertEqual(second.version, 2)
            self.assertEqual(service.skill_registry.get("global-skill").name, "Updated Skill")

            with self.assertRaises(SkillVersionConflictError) as raised:
                service.save_skill(updated, expected_version=1)
            self.assertEqual(raised.exception.actual, 2)

            self.assertTrue(service.delete_skill("global-skill"))
            self.assertIsNone(service.skill_registry.get("global-skill"))

    def test_workflow_tool_requires_explicit_system_reference(self) -> None:
        workflow = WorkflowDefinition.from_mapping(
            {
                "schema_version": 1,
                "id": "system-tool-flow",
                "name": "System Tool Flow",
                "description": "Execute one explicit system tool reference",
                "version": 1,
                "entry_node_id": "read",
                "nodes": [
                    {
                        "id": "read",
                        "type": "tool",
                        "name": "Read",
                        "config": {"provider": "system", "tool_name": "read_file"},
                    }
                ],
                "edges": [],
            }
        )
        self.assertTrue(validate_workflow(workflow, tool_names={"read_file"}).ok)

    def test_prompt_node_is_not_part_of_workflow_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported workflow node type"):
            WorkflowDefinition.from_mapping(
                {
                    "schema_version": 1,
                    "id": "prompt-flow",
                    "name": "Prompt Flow",
                    "description": "Invalid legacy prompt node",
                    "version": 1,
                    "entry_node_id": "prompt",
                    "nodes": [
                        {"id": "prompt", "type": "prompt", "name": "Prompt", "config": {}}
                    ],
                    "edges": [],
                }
            )


if __name__ == "__main__":
    unittest.main()

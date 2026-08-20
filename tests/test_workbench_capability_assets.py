from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_runtime.tools import build_tool_registry
from agent_runtime.workbench import (
    CapabilityAssetService,
    PromptVersionConflictError,
    ResourceScope,
    SkillVersionConflictError,
    WorkflowDefinition,
    validate_workflow,
)


def runtime_tool_names() -> set[str]:
    return {
        item.name
        for item in build_tool_registry().definitions(
            enabled_features=frozenset({"view_image"})
        )
    }


def capability_service(workspace: Path) -> tuple[CapabilityAssetService, Path]:
    global_root = workspace / "global-assets"
    return (
        CapabilityAssetService(
            known_tool_names=runtime_tool_names(),
            global_root=global_root,
        ),
        global_root,
    )


def prompt_payload(prompt_id: str = "global-prompt") -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": prompt_id,
        "name": "Global Prompt",
        "description": "Global managed prompt",
        "arguments": [
            {"name": "target", "description": "Target", "required": False}
        ],
        "messages": [
            {"role": "user", "content": "Inspect {{target}}"}
        ],
    }


def skill_payload(
    skill_id: str = "global-skill",
    *,
    entry_prompt: str = "global-prompt",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": skill_id,
        "name": "Global Skill",
        "description": "Global managed skill",
        "entry_prompt": entry_prompt,
        "tool_references": [
            {"provider": "system", "tool_name": "read_file"},
            {"provider": "system", "tool_name": "search_text"},
        ],
        "artifacts": ["report.md"],
        "method_document": "# Global Skill\n\n1. Read evidence.\n2. Produce report.",
    }


class CapabilityAssetDomainTests(unittest.TestCase):
    def test_prompt_store_crud_versions_and_registry_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service, _global_root = capability_service(Path(temporary))

            first = service.save_prompt(prompt_payload(), expected_version=0)
            self.assertEqual(first.version, 1)
            self.assertEqual(first.scope, ResourceScope.GLOBAL)
            self.assertEqual(
                service.prompt_registry.get("global-prompt").version,
                1,
            )

            updated = prompt_payload()
            updated["name"] = "Updated Prompt"
            second = service.save_prompt(updated, expected_version=1)
            self.assertEqual(second.version, 2)
            self.assertEqual(
                service.prompt_registry.get("global-prompt").name,
                "Updated Prompt",
            )

            with self.assertRaises(PromptVersionConflictError) as raised:
                service.save_prompt(updated, expected_version=1)
            self.assertEqual(raised.exception.actual, 2)

            self.assertTrue(service.delete_prompt("global-prompt"))
            self.assertIsNone(service.prompt_registry.get("global-prompt"))

    def test_prompt_global_override_delete_reveals_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service, _global_root = capability_service(Path(temporary))
            builtin = service.prompt_registry.get("project-analysis")
            assert builtin is not None
            self.assertEqual(builtin.scope, ResourceScope.BUILTIN)

            raw = builtin.to_dict()
            raw["name"] = "Global Project Analysis"
            saved = service.save_prompt(raw, expected_version=0)
            self.assertEqual(saved.version, 1)
            self.assertEqual(
                service.prompt_registry.get("project-analysis").scope,
                ResourceScope.GLOBAL,
            )

            self.assertTrue(service.delete_prompt("project-analysis"))
            restored = service.prompt_registry.get("project-analysis")
            assert restored is not None
            self.assertEqual(restored.scope, ResourceScope.BUILTIN)
            self.assertEqual(restored.name, builtin.name)

    def test_skill_store_crud_tool_references_and_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            service, global_root = capability_service(workspace)
            service.save_prompt(prompt_payload(), expected_version=0)

            first = service.save_skill(skill_payload(), expected_version=0)
            self.assertEqual(first.version, 1)
            self.assertEqual(first.scope, ResourceScope.GLOBAL)
            self.assertEqual(
                [item.key for item in first.tool_references],
                ["system:read_file", "system:search_text"],
            )

            metadata = json.loads(
                (global_root / "skills" / "global-skill" / "skill.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["tool_references"][0]["provider"], "system")
            self.assertEqual(metadata["tool_references"][0]["tool_name"], "read_file")

            updated = skill_payload()
            updated["name"] = "Updated Skill"
            second = service.save_skill(updated, expected_version=1)
            self.assertEqual(second.version, 2)
            self.assertEqual(
                service.skill_registry.get("global-skill").name,
                "Updated Skill",
            )

            with self.assertRaises(SkillVersionConflictError) as raised:
                service.save_skill(updated, expected_version=1)
            self.assertEqual(raised.exception.actual, 2)

            self.assertTrue(service.delete_skill("global-skill"))
            self.assertIsNone(service.skill_registry.get("global-skill"))

    def test_prompt_delete_is_blocked_while_skill_depends_on_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service, _global_root = capability_service(Path(temporary))
            service.save_prompt(prompt_payload(), expected_version=0)
            service.save_skill(skill_payload(), expected_version=0)

            with self.assertRaisesRegex(ValueError, "referenced by Skills"):
                service.delete_prompt("global-prompt")

            self.assertIsNotNone(service.prompt_store.get("global-prompt"))
            self.assertIsNotNone(service.prompt_registry.get("global-prompt"))

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
        node = workflow.nodes[0]
        self.assertEqual(node.config["provider"], "system")
        self.assertEqual(node.config["tool_name"], "read_file")
        self.assertTrue(validate_workflow(workflow, tool_names={"read_file"}).ok)

        invalid = {
            **workflow.to_dict(),
            "id": "missing-provider-flow",
            "nodes": [
                {
                    "id": "read",
                    "type": "tool",
                    "name": "Read",
                    "config": {"tool_name": "read_file"},
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "unsupported tool provider"):
            WorkflowDefinition.from_mapping(invalid)


if __name__ == "__main__":
    unittest.main()


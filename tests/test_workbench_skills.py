from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mcp_tools_server.runtime import Runtime
from mcp_tools_server.workbench import ResourceScope, build_prompt_registry, build_skill_registry


KNOWN_TOOLS = {
    "apply_patch",
    "server_info",
    "list_dir",
    "list_files",
    "read_file",
    "search_text",
    "exec_process",
    "git_diff",
    "git_show",
    "dangerous-demo-tool",
}


class SkillRegistryTests(unittest.TestCase):
    def test_builtin_skills_are_available_and_reference_prompts(self) -> None:
        prompts = build_prompt_registry()
        registry = build_skill_registry(
            prompt_registry=prompts,
            known_tool_names=KNOWN_TOOLS,
        )

        ids = {item.id for item in registry.list()}
        self.assertEqual(
            {
                "project-analysis",
                "bug-investigation",
                "reverse-engineering",
                "code-review",
                "spec-development",
                "release-validation",
            },
            ids,
        )
        reverse = registry.get("reverse-engineering")
        assert reverse is not None
        self.assertEqual(reverse.entry_prompt, "reverse-engineering")
        self.assertIn("Architecture Mapping", reverse.method_document)

    def test_global_skill_overrides_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            global_root = root / "global-skills"
            prompts = build_prompt_registry()
            skill_root = global_root / "code-review"
            skill_root.mkdir(parents=True)
            (skill_root / "skill.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "code-review",
                        "name": "Global Review",
                        "entry_prompt": "code-review",
                        "tool_references": [
                            {"provider": "system", "tool_name": "read_file"}
                        ],
                        "artifacts": ["review.md"],
                    }
                ),
                encoding="utf-8",
            )
            (skill_root / "SKILL.md").write_text("# Review\nRead first.", encoding="utf-8")

            registry = build_skill_registry(
                prompt_registry=prompts,
                known_tool_names=KNOWN_TOOLS,
                global_skill_roots=(global_root,),
            )

        skill = registry.get("code-review")
        assert skill is not None
        self.assertEqual(skill.name, "Global Review")
        self.assertEqual(skill.scope, ResourceScope.GLOBAL)

    def test_allowlist_can_only_reduce_available_tools(self) -> None:
        registry = build_skill_registry(
            prompt_registry=build_prompt_registry(),
            known_tool_names=KNOWN_TOOLS,
        )
        skill = registry.get("code-review")
        assert skill is not None
        effective = set(skill.effective_tools(frozenset({"read_file", "git_diff", "dangerous-demo-tool"})))
        self.assertEqual(effective, {"read_file", "git_diff"})
        self.assertNotIn("dangerous-demo-tool", effective)

    def test_unknown_tool_or_prompt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            global_skills = root / "global-skills"
            skill_root = global_skills / "bad-skill"
            skill_root.mkdir(parents=True)
            (skill_root / "skill.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "bad-skill",
                        "name": "Bad Skill",
                        "entry_prompt": "missing-prompt",
                        "tool_references": [
                            {"provider": "system", "tool_name": "missing-tool"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (skill_root / "SKILL.md").write_text("# Bad", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown prompt"):
                build_skill_registry(
                    prompt_registry=build_prompt_registry(),
                    known_tool_names=KNOWN_TOOLS,
                    global_skill_roots=(global_skills,),
                )


class SkillToolTests(unittest.TestCase):
    def test_skill_list_and_get_are_available_to_ai(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                listed = runtime.call_tool("skill_list", {})
                detail = runtime.call_tool("skill_get", {"skill_id": "reverse-engineering"})
            finally:
                runtime.close()

        self.assertTrue(listed["structuredContent"]["ok"])
        ids = {item["id"] for item in listed["structuredContent"]["skills"]}
        self.assertIn("reverse-engineering", ids)
        skill = detail["structuredContent"]["skill"]
        self.assertEqual(skill["entry_prompt"], "reverse-engineering")
        self.assertIn("search_text", skill["effective_tools"])


if __name__ == "__main__":
    unittest.main()

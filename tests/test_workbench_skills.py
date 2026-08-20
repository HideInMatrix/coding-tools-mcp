from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_runtime.runtime import Runtime
from agent_runtime.workbench import ResourceScope, build_skill_registry


class SkillRegistryTests(unittest.TestCase):
    def test_registry_has_no_default_skills(self) -> None:
        registry = build_skill_registry()
        self.assertEqual((), registry.list())

    def test_global_skill_is_loaded_without_default_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            global_root = Path(temporary) / "global-skills"
            skill_root = global_root / "code-review"
            skill_root.mkdir(parents=True)
            (skill_root / "skill.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "code-review",
                        "name": "Global Review",
                        "description": "Review changes",
                        "version": 1,
                        "artifacts": ["review.md"],
                    }
                ),
                encoding="utf-8",
            )
            (skill_root / "SKILL.md").write_text("# Review\nRead first.", encoding="utf-8")
            registry = build_skill_registry(global_skill_roots=(global_root,))

        skill = registry.get("code-review")
        assert skill is not None
        self.assertEqual(skill.name, "Global Review")
        self.assertEqual(skill.scope, ResourceScope.GLOBAL)


class SkillToolTests(unittest.TestCase):
    def test_skill_get_returns_not_found_without_user_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                detail = runtime.call_tool("skill_get", {"skill_id": "reverse-engineering"})
            finally:
                runtime.close()

        self.assertFalse(detail["structuredContent"]["ok"])


if __name__ == "__main__":
    unittest.main()

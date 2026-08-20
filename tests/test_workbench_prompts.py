from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_runtime.protocol import dispatch
from agent_runtime.runtime import Runtime
from agent_runtime.workbench import ResourceScope, build_prompt_registry


class PromptRegistryTests(unittest.TestCase):
    def test_builtin_prompts_are_available(self) -> None:
        registry = build_prompt_registry()

        ids = {item.id for item in registry.list()}
        self.assertIn("spec-development", ids)
        self.assertIn("bug-investigation", ids)
        self.assertIn("reverse-engineering", ids)

    def test_scope_precedence_is_global_over_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            global_prompts = root / "global-prompts"
            global_prompts.mkdir(parents=True)

            base = {
                "schema_version": 1,
                "id": "code-review",
                "description": "override",
                "messages": [{"role": "user", "content": "override"}],
            }
            (global_prompts / "code-review.json").write_text(
                json.dumps({**base, "name": "Global Review"}),
                encoding="utf-8",
            )
            global_registry = build_prompt_registry(
                global_prompt_roots=(global_prompts,),
            )
            global_prompt = global_registry.get("code-review")
            assert global_prompt is not None
            self.assertEqual(global_prompt.name, "Global Review")
            self.assertEqual(global_prompt.scope, ResourceScope.GLOBAL)

    def test_prompt_render_requires_declared_arguments(self) -> None:
        registry = build_prompt_registry()

        rendered = registry.render("bug-investigation", {"symptom": "startup fails"})
        text = rendered["messages"][0]["content"]["text"]
        self.assertIn("startup fails", text)
        with self.assertRaisesRegex(ValueError, "missing required"):
            registry.render("bug-investigation", {})


class PromptProtocolTests(unittest.TestCase):
    def test_initialize_declares_prompt_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                response = dispatch(
                    runtime,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-11-25"},
                    },
                )
            finally:
                runtime.close()

        assert response is not None
        self.assertEqual(
            response["result"]["capabilities"]["prompts"],
            {"listChanged": False},
        )

    def test_prompts_list_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                listed = dispatch(
                    runtime,
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "prompts/list",
                        "params": {},
                    },
                )
                rendered = dispatch(
                    runtime,
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "prompts/get",
                        "params": {
                            "name": "spec-development",
                            "arguments": {"feature": "workflow editor"},
                        },
                    },
                )
            finally:
                runtime.close()

        assert listed is not None
        assert rendered is not None
        names = {item["name"] for item in listed["result"]["prompts"]}
        self.assertIn("spec-development", names)
        self.assertIn(
            "workflow editor",
            rendered["result"]["messages"][0]["content"]["text"],
        )

    def test_modern_server_discover_declares_prompts_and_get_validates_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                discovered = dispatch(
                    runtime,
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "server/discover",
                        "params": {
                            "_meta": {
                                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                "io.modelcontextprotocol/clientCapabilities": {},
                            }
                        },
                    },
                )
                invalid = dispatch(
                    runtime,
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "prompts/get",
                        "params": {
                            "name": "bug-investigation",
                            "arguments": {},
                        },
                    },
                )
            finally:
                runtime.close()

        assert discovered is not None
        assert invalid is not None
        self.assertEqual(
            discovered["result"]["capabilities"]["prompts"],
            {"listChanged": False},
        )
        self.assertEqual(invalid["error"]["code"], -32602)
        self.assertIn("missing required prompt argument", invalid["error"]["message"])


if __name__ == "__main__":
    unittest.main()

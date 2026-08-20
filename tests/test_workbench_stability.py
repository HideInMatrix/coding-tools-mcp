from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mcp_tools_server.runtime import Runtime
from mcp_tools_server.tools import build_tool_registry
from mcp_tools_server.workbench import (
    PromptDefinition,
    ResourceScope,
    RunStore,
    SkillDefinition,
    ArtifactStore,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowRun,
    WorkflowStore,
    build_prompt_registry,
    build_skill_registry,
    build_workflow_registry,
    validate_workflow,
)


def simple_workflow(workflow_id: str = "stable-flow") -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": workflow_id,
        "name": "Stable Flow",
        "description": "stability fixture",
        "version": 1,
        "entry_node_id": "approval",
        "nodes": [
            {
                "id": "approval",
                "type": "approval",
                "name": "Approval",
                "position": {"x": 10, "y": 20},
                "config": {"title": "Continue"},
            }
        ],
        "edges": [],
        "metadata": {},
    }


class WorkbenchSchemaVersionTests(unittest.TestCase):
    def test_missing_and_pre_release_schema_versions_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "prompt schema_version is required"):
            PromptDefinition.from_mapping(
                {
                    "id": "missing-schema-prompt",
                    "name": "Missing Schema Prompt",
                    "messages": [{"role": "user", "content": "hello"}],
                },
                scope=ResourceScope.GLOBAL,
                source="test",
            )

        with self.assertRaisesRegex(ValueError, "unsupported skill schema_version: 0"):
            SkillDefinition.from_mapping(
                {
                    "schema_version": 0,
                    "id": "pre-release-skill",
                    "name": "Pre-release Skill",
                    "tool_references": [],
                },
                method_document="# Pre-release",
                scope=ResourceScope.GLOBAL,
                source="test",
            )

        workflow_payload = simple_workflow()
        workflow_payload.pop("schema_version")
        with self.assertRaisesRegex(ValueError, "workflow schema_version is required"):
            WorkflowDefinition.from_mapping(workflow_payload)

    def test_future_schema_is_rejected_for_all_persisted_types(self) -> None:
        with self.assertRaisesRegex(ValueError, "future prompt schema_version"):
            PromptDefinition.from_mapping(
                {
                    "schema_version": 999,
                    "id": "future-prompt",
                    "messages": [{"role": "user", "content": "future"}],
                },
                scope=ResourceScope.GLOBAL,
                source="test",
            )

        with self.assertRaisesRegex(ValueError, "future skill schema_version"):
            SkillDefinition.from_mapping(
                {
                    "schema_version": 999,
                    "id": "future-skill",
                    "tool_references": [],
                },
                method_document="# Future",
                scope=ResourceScope.GLOBAL,
                source="test",
            )

        workflow = simple_workflow()
        workflow["schema_version"] = 999
        with self.assertRaisesRegex(ValueError, "future workflow schema_version"):
            WorkflowDefinition.from_mapping(workflow)

        with self.assertRaisesRegex(ValueError, "future workflow_run schema_version"):
            WorkflowRun.from_mapping(
                {
                    "schema_version": 999,
                    "run_id": "a" * 24,
                    "workflow_id": "stable-flow",
                    "workflow_version": 1,
                    "workflow_scope": "workspace",
                    "workflow_snapshot": simple_workflow(),
                    "workspace": "/tmp",
                    "status": "pending",
                    "engine_state": {
                        "activated": [],
                        "ready": [],
                        "completed": [],
                        "outcomes": {},
                        "outputs": {},
                    },
                }
            )


class WorkbenchCorruptionRecoveryTests(unittest.TestCase):
    def test_corrupt_prompt_is_skipped_without_breaking_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prompt_dir = Path(temporary) / "global-prompts"
            prompt_dir.mkdir(parents=True)
            (prompt_dir / "broken.json").write_text("{broken", encoding="utf-8")

            registry = build_prompt_registry(global_prompt_roots=(prompt_dir,))
            self.assertIsNotNone(registry.get("project-analysis"))
            self.assertIsNone(registry.get("broken"))
            self.assertTrue((prompt_dir / ".quarantine" / "broken.json").is_file())

    def test_corrupt_skill_is_skipped_without_breaking_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            global_skills = Path(temporary) / "global-skills"
            skill_dir = global_skills / "broken"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.json").write_text("{broken", encoding="utf-8")
            (skill_dir / "SKILL.md").write_text("# Broken", encoding="utf-8")
            prompts = build_prompt_registry()
            known_tools = {
                item.name
                for item in build_tool_registry().definitions(
                    enabled_features=frozenset({"view_image"})
                )
            }
            registry = build_skill_registry(
                prompt_registry=prompts,
                known_tool_names=known_tools,
                global_skill_roots=(global_skills,),
            )
            self.assertIsNotNone(registry.get("reverse-engineering"))
            self.assertIsNone(registry.get("broken"))
            self.assertTrue((global_skills / ".quarantine" / "broken").is_dir())

    def test_corrupt_workflow_is_quarantined_and_registry_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            workflow_dir = workspace / ".coding-tools" / "workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "broken.json").write_text("{broken", encoding="utf-8")
            store = WorkflowStore(workspace)

            self.assertEqual(store.list(), ())
            self.assertIsNone(store.get("broken"))
            self.assertTrue((workflow_dir / ".quarantine" / "broken.json").is_file())
            registry = build_workflow_registry(store=store)

        self.assertIsNotNone(registry.get("project-development"))

    def test_future_workflow_schema_is_skipped_without_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            workflow_dir = workspace / ".coding-tools" / "workflows"
            workflow_dir.mkdir(parents=True)
            future = workflow_dir / "future.json"
            future.write_text(
                json.dumps({
                    "schema_version": 999,
                    "id": "future",
                    "name": "Future",
                    "entry_node_id": "node",
                    "nodes": [],
                    "edges": [],
                }),
                encoding="utf-8",
            )
            store = WorkflowStore(workspace)

            self.assertEqual(store.list(), ())
            self.assertTrue(future.is_file())
            self.assertFalse((workflow_dir / ".quarantine" / "future.json").exists())

    def test_corrupt_run_is_skipped_but_direct_get_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            run_id = "b" * 24
            run_dir = workspace / ".coding-tools" / "runs" / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text("{broken", encoding="utf-8")
            store = RunStore(workspace)

            self.assertEqual(store.list(), ())
            with self.assertRaisesRegex(RuntimeError, "Workflow Run 文件损坏"):
                store.get(run_id)


class WorkflowImportExportTests(unittest.TestCase):
    def test_export_import_round_trip_uses_same_validator_and_version_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runtime = Runtime(workspace)
            try:
                raw = simple_workflow("portable-flow")
                saved = runtime.call_tool(
                    "workflow_save",
                    {"workflow": raw, "expected_version": 0},
                )["structuredContent"]
                self.assertTrue(saved["saved"])

                exported = runtime.call_tool(
                    "workflow_export",
                    {"workflow_id": "portable-flow"},
                )["structuredContent"]
                document = json.loads(exported["document"])
                document["name"] = "Imported Flow"

                imported = runtime.call_tool(
                    "workflow_import",
                    {
                        "document": json.dumps(document),
                        "expected_version": 1,
                    },
                )["structuredContent"]
            finally:
                runtime.close()

        self.assertTrue(imported["saved"])
        self.assertEqual(imported["workflow"]["name"], "Imported Flow")
        self.assertEqual(imported["workflow"]["version"], 2)

    def test_import_rejects_invalid_json_and_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                invalid = runtime.call_tool(
                    "workflow_import",
                    {"document": "{broken", "expected_version": 0},
                )["structuredContent"]
                raw = simple_workflow("secret-flow")
                raw["metadata"] = {"api_key": "plaintext-secret"}
                secret = runtime.call_tool(
                    "workflow_import",
                    {
                        "document": json.dumps(raw),
                        "expected_version": 0,
                    },
                )["structuredContent"]
            finally:
                runtime.close()

        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["error"]["code"], "WORKFLOW_IMPORT_INVALID_JSON")
        self.assertFalse(secret["ok"])
        self.assertEqual(secret["error"]["code"], "WORKFLOW_SECRET")


class RunPruningTests(unittest.TestCase):
    def test_prune_removes_old_terminal_runs_but_keeps_nonterminal_and_failed(self) -> None:
        from mcp_tools_server.workbench.engine import EngineState

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            store = RunStore(workspace)
            now = int(time.time())
            statuses = [
                "succeeded",
                "cancelled",
                "succeeded",
                "failed",
                "waiting_model",
            ]
            run_ids: list[str] = []
            for index, status in enumerate(statuses):
                run_id = f"{index + 1:024x}"
                run_ids.append(run_id)
                store.save(
                    WorkflowRun(
                        run_id=run_id,
                        workflow_id="stable-flow",
                        workflow_version=1,
                        workflow_scope="workspace",
                        workflow_snapshot=simple_workflow(),
                        workspace=str(workspace),
                        status=status,
                        engine_state=EngineState(activated=(), ready=()),
                        created_at=now + index,
                        updated_at=now + index,
                    )
                )

            deleted = store.prune(max_runs=3)
            remaining = {run.run_id: run.status for run in store.list()}

        self.assertEqual(set(deleted), {run_ids[0], run_ids[1]})
        self.assertEqual(len(remaining), 3)
        self.assertEqual(remaining[run_ids[3]], "failed")
        self.assertEqual(remaining[run_ids[4]], "waiting_model")


class WorkbenchPathAndScaleTests(unittest.TestCase):
    def test_unicode_and_space_workspace_paths_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "公司 项目 Workspace"
            workspace.mkdir()
            store = WorkflowStore(workspace)
            saved = store.save(
                WorkflowDefinition.from_mapping(simple_workflow("unicode-flow")),
                expected_version=0,
            )
            loaded = store.get("unicode-flow")

        assert loaded is not None
        self.assertEqual(saved.version, 1)
        self.assertEqual(loaded.id, "unicode-flow")
        self.assertIn("公司 项目 Workspace", str(store.workspace))

    def test_resource_ids_reject_posix_windows_and_traversal_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            workflow_store = WorkflowStore(workspace)
            run_store = RunStore(workspace)
            artifact_store = ArtifactStore(workspace)

            for invalid in ("../outside", "a/b", r"a\b", "C:drive", ".."):
                with self.subTest(workflow_id=invalid):
                    with self.assertRaises(ValueError):
                        workflow_store.get(invalid)

            for invalid in ("../run", "a/b", r"a\b", "C:run", "short"):
                with self.subTest(run_id=invalid):
                    with self.assertRaises(ValueError):
                        run_store.get(invalid)

            with self.assertRaises(ValueError):
                artifact_store.write(
                    run_id="d" * 24,
                    artifact_id="../escape",
                    producer_node_id="producer",
                    value={"ok": True},
                    format="json",
                )

    def test_five_thousand_node_dag_validates_without_recursion(self) -> None:
        node_count = 5_000
        nodes = [
            {
                "id": f"n-{index}",
                "type": "approval",
                "name": f"Node {index}",
                "config": {"title": f"Node {index}"},
            }
            for index in range(node_count)
        ]
        edges = [
            {
                "id": f"e-{index}",
                "source": f"n-{index}",
                "target": f"n-{index + 1}",
                "condition": "approved",
            }
            for index in range(node_count - 1)
        ]
        workflow = WorkflowDefinition.from_mapping(
            {
                "schema_version": 1,
                "id": "large-dag",
                "name": "Large DAG",
                "description": "Large graph validation performance fixture",
                "version": 1,
                "entry_node_id": "n-0",
                "nodes": nodes,
                "edges": edges,
            }
        )

        started = time.perf_counter()
        result = validate_workflow(workflow)
        elapsed = time.perf_counter() - started

        self.assertTrue(result.ok)
        self.assertLess(elapsed, 5.0)

    def test_two_thousand_workflow_catalog_lists_and_summarizes_quickly(self) -> None:
        registry = WorkflowRegistry()
        for index in range(2_000):
            registry.register(
                WorkflowDefinition.from_mapping(
                    {
                        "schema_version": 1,
                        "id": f"catalog-{index}",
                        "name": f"Catalog {index}",
                        "description": "Large catalog performance fixture",
                        "version": 1,
                        "entry_node_id": "approval",
                        "nodes": [
                            {
                                "id": "approval",
                                "type": "approval",
                                "name": "Approval",
                                "config": {"title": "Approve"},
                            }
                        ],
                        "edges": [],
                    }
                )
            )

        started = time.perf_counter()
        summaries = [item.summary() for item in registry.list()]
        elapsed = time.perf_counter() - started

        self.assertEqual(len(summaries), 2_000)
        self.assertLess(elapsed, 2.0)


class WorkbenchPermissionRegressionTests(unittest.TestCase):
    def test_workflow_tool_node_does_not_bypass_safe_git_write_permission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runtime = Runtime(workspace, permission_mode="safe")
            try:
                workflow = WorkflowDefinition.from_mapping(
                    {
                        "schema_version": 1,
                        "id": "safe-permission-flow",
                        "name": "Safe Permission Flow",
                        "description": "Verify workflow tools preserve safe permission boundaries",
                        "version": 1,
                        "entry_node_id": "git",
                        "nodes": [
                            {
                                "id": "git",
                                "type": "tool",
                                "name": "Git write attempt",
                                "config": {
                                    "provider": "system",
                                    "tool_name": "exec_command",
                                    "arguments": {"cmd": "git commit -m should-not-run"},
                                },
                            }
                        ],
                        "edges": [],
                    }
                )
                saved = runtime.workflow_store.save(workflow, expected_version=0)
                runtime.workflow_registry.register(saved, replace=True)
                run = runtime.workflow_runs.start("safe-permission-flow")
            finally:
                runtime.close()

        self.assertEqual(run.status, "failed")
        self.assertIn("git", run.node_states)
        self.assertEqual(run.node_states["git"]["status"], "failed")
        tool_output = run.engine_state.outputs.get("git")
        self.assertIsInstance(tool_output, dict)
        structured = tool_output.get("structuredContent") if isinstance(tool_output, dict) else None
        self.assertIsInstance(structured, dict)
        self.assertEqual(structured["error"]["details"]["permission"], "git_metadata_write")
if __name__ == "__main__":
    unittest.main()

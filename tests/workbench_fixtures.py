from __future__ import annotations

from typing import Any

from agent_runtime.workbench import ResourceScope, SkillDefinition, WorkflowDefinition


def install_project_development_fixture(runtime: Any) -> WorkflowDefinition:
    skill = SkillDefinition.from_mapping(
        {
            "schema_version": 1,
            "id": "test-spec-development",
            "name": "Test Spec Development",
            "description": "Test-only model action skill",
            "version": 1,
            "artifacts": ["project-delivery-report.json"],
        },
        method_document="# Test Spec Development\n\nProduce a deterministic test result.",
        scope=ResourceScope.GLOBAL,
        source="test-fixture",
    )
    runtime.skill_registry.register(skill, replace=True)

    workflow = WorkflowDefinition.from_mapping(
        {
            "schema_version": 1,
            "id": "project-development",
            "name": "Project Development Fixture",
            "description": "Test-only model, artifact, and approval workflow",
            "version": 1,
            "entry_node_id": "work",
            "inputs_schema": {
                "type": "object",
                "properties": {"feature": {"type": "string"}},
                "additionalProperties": True,
            },
            "nodes": [
                {
                    "id": "work",
                    "type": "skill",
                    "name": "Work",
                    "config": {"skill_id": "test-spec-development"},
                },
                {
                    "id": "report",
                    "type": "artifact",
                    "name": "Report",
                    "config": {
                        "artifact_id": "project-delivery-report",
                        "source_node_id": "work",
                        "format": "json",
                    },
                },
                {
                    "id": "approval",
                    "type": "approval",
                    "name": "Approval",
                    "config": {"title": "Confirm"},
                },
            ],
            "edges": [
                {"id": "work-report", "source": "work", "target": "report"},
                {"id": "report-approval", "source": "report", "target": "approval"},
            ],
        }
    )
    saved = runtime.workflow_store.save(workflow)
    runtime.workflow_registry.register(saved, replace=True)
    return saved

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime.tools import build_tool_registry
from agent_runtime.workbench import (
    CapabilityAssetService,
    MCPConnectionService,
    RunStore,
    WorkflowDefinition,
    WorkflowStore,
    build_effective_tool_catalog,
    build_workflow_registry,
    is_workbench_control_tool,
    validate_workflow,
)

from .gateway_manager import MCPGatewayManager
from .gateway_profiles import GatewayProfileStore, MCPGatewayMember
from .server_manager import MCPServerManager
from .server_profiles import MCPServerProfile, ServerProfileStore


@dataclass(frozen=True, slots=True)
class WorkbenchTarget:
    target_id: str
    server_id: str
    service_name: str
    profile_name: str
    workspace: Path
    running: bool
    enable_view_image: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "server_id": self.server_id,
            "service_name": self.service_name,
            "profile_name": self.profile_name,
            "workspace": str(self.workspace),
            "running": self.running,
        }


class DesktopWorkbenchManager:
    def __init__(
        self,
        *,
        server_store: ServerProfileStore,
        gateway_store: GatewayProfileStore,
        server_manager: MCPServerManager,
        gateway_manager: MCPGatewayManager,
        global_root: Path | None = None,
    ) -> None:
        self.server_store = server_store
        self.gateway_store = gateway_store
        self.server_manager = server_manager
        self.gateway_manager = gateway_manager
        self.global_root = global_root.resolve() if global_root is not None else None

    def targets(self) -> tuple[WorkbenchTarget, ...]:
        targets: list[WorkbenchTarget] = []
        for profile in self.server_store.list():
            targets.append(
                WorkbenchTarget(
                    target_id=f"server:{profile.server_id}",
                    server_id=profile.server_id,
                    service_name=profile.name,
                    profile_name="主 Workspace",
                    workspace=profile.workspace.resolve(),
                    running=self.server_manager.is_running(profile.server_id),
                    enable_view_image=profile.enable_view_image,
                )
            )
        for gateway in self.gateway_store.list():
            gateway_running = self.gateway_manager.is_running(gateway.gateway_id)
            for member in gateway.members:
                targets.append(
                    WorkbenchTarget(
                        target_id=f"gateway:{gateway.gateway_id}:{member.server_id}",
                        server_id=member.server_id,
                        service_name=gateway.name,
                        profile_name=member.name,
                        workspace=member.workspace.resolve(),
                        running=gateway_running
                        and (gateway.mode == "multi" or member.instance_path == ""),
                        enable_view_image=member.enable_view_image,
                    )
                )
        return tuple(targets)

    def target(self, target_id: str) -> WorkbenchTarget:
        for target in self.targets():
            if target.target_id == target_id.strip():
                return target
        raise KeyError(f"找不到 Workbench Target: {target_id}")

    @staticmethod
    def _tool_names(*, enable_view_image: bool) -> set[str]:
        return {
            item.name
            for item in DesktopWorkbenchManager._system_tool_definitions(
                enable_view_image=enable_view_image
            )
        }

    @staticmethod
    def _system_tool_definitions(*, enable_view_image: bool):
        features = frozenset({"view_image"}) if enable_view_image else frozenset()
        return tuple(
            item
            for item in build_tool_registry().definitions(enabled_features=features)
            if not is_workbench_control_tool(item.name)
        )

    @staticmethod
    def _all_system_tool_names() -> set[str]:
        return DesktopWorkbenchManager._tool_names(enable_view_image=True)

    def _mcp_connection_service(self) -> MCPConnectionService:
        return MCPConnectionService(global_root=self.global_root)

    def _global_asset_service(self) -> tuple[CapabilityAssetService, set[str]]:
        tool_names = self._all_system_tool_names()
        mcp_tool_keys = self._mcp_connection_service().tool_keys()
        assets = CapabilityAssetService(
            known_tool_names=tool_names,
            known_mcp_tool_keys=mcp_tool_keys,
            global_root=self.global_root,
        )
        return assets, tool_names

    def _asset_service(self, target: WorkbenchTarget) -> tuple[CapabilityAssetService, set[str]]:
        tool_names = self._tool_names(
            enable_view_image=target.enable_view_image,
        )
        assets = CapabilityAssetService(
            known_tool_names=self._all_system_tool_names(),
            known_mcp_tool_keys=self._mcp_connection_service().tool_keys(),
            global_root=self.global_root,
        )
        return assets, tool_names

    def _context(self, target: WorkbenchTarget):
        assets, tool_names = self._asset_service(target)
        prompts = assets.prompt_registry
        skills = assets.skill_registry
        store = WorkflowStore(target.workspace)
        workflows = build_workflow_registry(store=store)
        return prompts, skills, tool_names, store, workflows

    def catalog(self, target_id: str) -> dict[str, object]:
        target = self.target(target_id)
        prompts, skills, tools, _store, workflows = self._context(target)
        connections = self._mcp_connection_service().list()
        effective_tools = build_effective_tool_catalog(
            self._system_tool_definitions(enable_view_image=target.enable_view_image),
            connections,
        )
        return {
            "target": target.to_dict(),
            "prompts": [
                {
                    "id": item.id,
                    "name": item.name,
                    "description": item.description,
                    "version": item.version,
                    "scope": item.scope.value,
                    "arguments": [arg.mcp_definition() for arg in item.arguments],
                }
                for item in prompts.list()
            ],
            "skills": [item.summary() for item in skills.list()],
            "tools": sorted(tools),
            "effective_tools": [item.to_dict() for item in effective_tools],
            "mcp_connections": [item.summary() for item in connections],
            "workflows": [item.summary() for item in workflows.list()],
        }

    def capability_catalog(self) -> dict[str, object]:
        assets, tools = self._global_asset_service()
        connections = self._mcp_connection_service().list()
        effective_tools = build_effective_tool_catalog(
            self._system_tool_definitions(enable_view_image=True),
            connections,
        )
        return {
            "prompts": [
                {
                    "id": item.id,
                    "name": item.name,
                    "description": item.description,
                    "version": item.version,
                    "scope": item.scope.value,
                    "arguments": [arg.mcp_definition() for arg in item.arguments],
                }
                for item in assets.prompt_registry.list()
            ],
            "skills": [item.summary() for item in assets.skill_registry.list()],
            "tools": sorted(tools),
            "effective_tools": [item.to_dict() for item in effective_tools],
            "mcp_connections": [item.summary() for item in connections],
        }

    def get_mcp_connection(self, connection_id: str) -> dict[str, object]:
        definition = self._mcp_connection_service().get(connection_id.strip())
        if definition is None:
            raise KeyError(f"找不到 MCP Connection: {connection_id}")
        return definition.to_dict()

    def validate_mcp_connection(self, raw: dict[str, Any]) -> dict[str, object]:
        definition = self._mcp_connection_service().validate(raw)
        return {"ok": True, "connection": definition.to_dict()}

    def save_mcp_connection(
        self,
        raw: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, object]:
        saved = self._mcp_connection_service().save(
            raw,
            expected_version=expected_version,
        )
        return {"ok": True, "saved": True, "connection": saved.to_dict()}

    def delete_mcp_connection(self, connection_id: str) -> bool:
        assets, _tool_names = self._global_asset_service()
        return self._mcp_connection_service().delete(
            connection_id.strip(),
            skill_definitions=assets.skill_registry.list(),
        )

    def test_mcp_connection(
        self,
        connection_id: str,
        *,
        timeout_seconds: int = 8,
    ) -> dict[str, object]:
        probe = self._mcp_connection_service().test(
            connection_id.strip(),
            timeout=float(timeout_seconds),
        )
        return {
            "ok": probe.ok,
            "connection_id": connection_id.strip(),
            "protocol_version": probe.protocol_version,
            "elapsed_ms": probe.elapsed_ms,
            "error": probe.error,
        }

    def discover_mcp_connection_tools(
        self,
        connection_id: str,
        *,
        timeout_seconds: int = 8,
    ) -> dict[str, object]:
        service = self._mcp_connection_service()
        persisted, probe = service.discover(
            connection_id.strip(),
            timeout=float(timeout_seconds),
        )
        effective_tools = build_effective_tool_catalog(
            self._system_tool_definitions(enable_view_image=True),
            service.list(),
        )
        return {
            "ok": probe.ok,
            "connection": persisted.to_dict(),
            "tools": [item.to_dict() for item in probe.tools],
            "effective_tools": [item.to_dict() for item in effective_tools],
            "protocol_version": probe.protocol_version,
            "elapsed_ms": probe.elapsed_ms,
            "error": probe.error,
        }

    def get_prompt(self, prompt_id: str) -> dict[str, object]:
        assets, _tool_names = self._global_asset_service()
        definition = assets.prompt_registry.get(prompt_id.strip())
        if definition is None:
            raise KeyError(f"找不到 Prompt: {prompt_id}")
        return {
            **definition.to_dict(),
            "scope": definition.scope.value,
            "source": definition.source,
        }

    def validate_prompt(self, raw: dict[str, Any]) -> dict[str, object]:
        assets, _tool_names = self._global_asset_service()
        definition = assets.validate_prompt(raw)
        return {
            "ok": True,
            "prompt": {
                **definition.to_dict(),
                "scope": definition.scope.value,
            },
        }

    def save_prompt(
        self,
        raw: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, object]:
        assets, _tool_names = self._global_asset_service()
        saved = assets.save_prompt(raw, expected_version=expected_version)
        return {
            "ok": True,
            "saved": True,
            "prompt": {
                **saved.to_dict(),
                "scope": saved.scope.value,
                "source": saved.source,
            },
        }

    def delete_prompt(self, prompt_id: str) -> bool:
        assets, _tool_names = self._global_asset_service()
        return assets.delete_prompt(prompt_id.strip())

    def get_skill(self, skill_id: str) -> dict[str, object]:
        assets, _tool_names = self._global_asset_service()
        definition = assets.skill_registry.get(skill_id.strip())
        if definition is None:
            raise KeyError(f"找不到 Skill: {skill_id}")
        return definition.to_dict()

    def validate_skill(self, raw: dict[str, Any]) -> dict[str, object]:
        assets, _tool_names = self._global_asset_service()
        definition = assets.validate_skill(raw)
        return {"ok": True, "skill": definition.to_dict()}

    def save_skill(
        self,
        raw: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, object]:
        assets, _tool_names = self._global_asset_service()
        saved = assets.save_skill(raw, expected_version=expected_version)
        return {"ok": True, "saved": True, "skill": saved.to_dict()}

    def delete_skill(self, skill_id: str) -> bool:
        assets, _tool_names = self._global_asset_service()
        return assets.delete_skill(skill_id.strip())

    def get_workflow(self, target_id: str, workflow_id: str) -> dict[str, object]:
        target = self.target(target_id)
        *_prefix, workflows = self._context(target)
        workflow = workflows.get(workflow_id.strip())
        if workflow is None:
            raise KeyError(f"找不到 Workflow: {workflow_id}")
        return {**workflow.to_dict(), "scope": workflow.scope.value}

    def validate_workflow(self, target_id: str, raw: dict[str, Any]) -> dict[str, object]:
        target = self.target(target_id)
        prompts, skills, tools, _store, _workflows = self._context(target)
        effective_tools = build_effective_tool_catalog(
            self._system_tool_definitions(enable_view_image=target.enable_view_image),
            self._mcp_connection_service().list(),
        )
        workflow = WorkflowDefinition.from_mapping(raw)
        result = validate_workflow(
            workflow,
            prompt_ids={item.id for item in prompts.list()},
            skill_ids={item.id for item in skills.list()},
            tool_names=set(tools),
            tool_keys={item.key for item in effective_tools},
        )
        return {**result.to_dict(), "workflow": workflow.to_dict()}

    def save_workflow(
        self,
        target_id: str,
        raw: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, object]:
        target = self.target(target_id)
        prompts, skills, tools, store, _workflows = self._context(target)
        effective_tools = build_effective_tool_catalog(
            self._system_tool_definitions(enable_view_image=target.enable_view_image),
            self._mcp_connection_service().list(),
        )
        workflow = WorkflowDefinition.from_mapping(raw)
        result = validate_workflow(
            workflow,
            prompt_ids={item.id for item in prompts.list()},
            skill_ids={item.id for item in skills.list()},
            tool_names=set(tools),
            tool_keys={item.key for item in effective_tools},
        )
        if not result.ok:
            return {**result.to_dict(), "saved": False, "workflow": workflow.to_dict()}
        saved = store.save(workflow, expected_version=expected_version)
        return {**result.to_dict(), "saved": True, "workflow": saved.to_dict()}

    def delete_workflow(self, target_id: str, workflow_id: str) -> bool:
        return WorkflowStore(self.target(target_id).workspace).delete(workflow_id)

    def runs(self, target_id: str) -> list[dict[str, object]]:
        return [run.public_dict() for run in RunStore(self.target(target_id).workspace).list()]

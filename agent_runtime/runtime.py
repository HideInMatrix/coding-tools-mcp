"""Business runtime for the project-owned MicroMatrix Workbench tools."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .core.constants import SERVER_NAME, SERVER_TITLE
from .core.dispatcher import ToolDispatcher
from .errors import RpcError, ToolError
from .processes import CommandManager
from .project_context import ProjectContext, load_project_context
from .local_permission_broker import LocalWorkflowApprovalBrokerClient
from .oauth_service import OAuthService
from .protocol import ACTIVE_REQUEST_CONTEXT, RequestContext, current_request_context
from .permissions.capabilities import (
    ELICITABLE_PERMISSIONS,
    PERMISSION_MODES,
    permission_profile,
)
from .permissions.context import ACTIVE_PERMISSIONS
from .permissions.policy import PermissionPolicy
from .permissions.session import PermissionSession
from .permissions.state import arguments_digest
from .results import make_tool_result
from .sandbox import build_sandbox_profile, create_process_sandbox
from .tools import build_tool_registry
from .tools.filesystem.handlers import FilesystemHandlers
from .tools.git.handlers import GitHandlers
from .tools.process.handlers import ProcessHandlers
from .tools.system.handlers import SystemHandlers
from .tools.toolchains.handlers import ToolchainHandlers
from .tools.workbench.handlers import WorkbenchHandlers
from .toolchains import ToolchainResolver
from .workspace import Workspace
from .workbench.capability_assets import CapabilityAssetService
from .workbench.engine import WorkflowEngine
from .workbench.mcp_connection_service import MCPConnectionService
from .workbench.registry import build_workflow_registry
from .workbench.runs import WorkflowRunManager
from .workbench.store import WorkflowStore


LOGGER = logging.getLogger(__name__)


class Runtime(
    FilesystemHandlers,
    ProcessHandlers,
    GitHandlers,
    SystemHandlers,
    ToolchainHandlers,
    WorkbenchHandlers,
):
    def __init__(
        self,
        workspace: Path,
        *,
        permission_mode: str = "safe",
        allow_network: bool = False,
        auth_token: str | None = None,
        oauth_service: OAuthService | None = None,
        enable_view_image: bool = True,
        fake_readonly_annotations: bool = False,
        project_context: ProjectContext | None = None,
        permission_broker: Any | None = None,
        permission_broker_from_env: bool = True,
        global_asset_root: Path | None = None,
    ) -> None:
        if permission_mode not in PERMISSION_MODES:
            raise ValueError(f"unknown permission mode: {permission_mode}")
        if fake_readonly_annotations and permission_mode != "dangerous":
            raise ValueError("fake_readonly_annotations requires dangerous permission mode")
        self.workspace = Workspace(workspace)
        self.tool_registry = build_tool_registry()
        self.mcp_connections = MCPConnectionService(global_root=global_asset_root)
        self.capability_assets = CapabilityAssetService(global_root=global_asset_root)
        self.skill_registry = self.capability_assets.skill_registry
        self.workflow_store = WorkflowStore(self.workspace.root)
        self.workflow_registry = build_workflow_registry(
            store=self.workflow_store,
        )
        self.permission_mode = permission_mode
        self.permission_profile = permission_profile(permission_mode)
        self.permission_policy = PermissionPolicy(self.permission_profile)
        self.allow_network = allow_network or permission_mode in {"trusted", "dangerous"}
        self.auth_token = auth_token
        self.oauth_service = oauth_service
        self.enable_view_image = enable_view_image
        self.fake_readonly_annotations = fake_readonly_annotations
        self.project_context = project_context or load_project_context(self.workspace.root)
        self.commands = CommandManager(self.workspace.root)
        self.permission_session = PermissionSession(
            self.workspace.root,
            broker_client=permission_broker,
            load_broker_from_env=permission_broker_from_env,
        )
        self._privileged_program_misses: set[str] = set()
        self._privileged_toolchain_misses: set[str] = set()
        self.safe_exec_path = ToolchainResolver.default_search_path(self.workspace.root)
        self.toolchain_read_roots: list[Path] = []
        sandbox_readable_roots = self._platform_read_roots()
        sandbox_writable_roots = [
            self.commands.runtime_dir,
            self.commands.home_dir,
            self.commands.tmp_dir,
            self.commands.cache_dir,
        ]
        sandbox_protected_paths = [
            path
            for path in (self.workspace.root / ".git",)
            if path.exists()
        ]
        # Build the baseline sandbox before discovery. Tool lookup and version
        # probes must observe the same filesystem/PATH restrictions as commands.
        self.process_sandbox = create_process_sandbox(
            mode=self.permission_mode,
            workspace=self.workspace.root,
            runtime_dir=self.commands.runtime_dir,
            readable_roots=sandbox_readable_roots,
            writable_roots=sandbox_writable_roots,
            protected_paths=sandbox_protected_paths,
            network=self.allow_network,
        )
        self.toolchains = ToolchainResolver(
            self.workspace.root,
            safe_path=self.safe_exec_path,
            probe_runner=self._run_toolchain_probe,
        )
        self._toolchain_snapshot = self.toolchains.discover(
            privileged=self.permission_mode == "dangerous"
        )
        self.safe_exec_path = [
            str(item) for item in self._toolchain_snapshot.get("safe_path", [])
        ]
        self.toolchain_read_roots = self._selected_toolchain_roots()
        sandbox_readable_roots = [
            *self.toolchain_read_roots,
            *self._platform_read_roots(),
        ]
        # Rebuild once with the roots actually proven executable by the
        # sandboxed probes. No guessed NVM/FNM/Mise directories are admitted.
        self.process_sandbox = create_process_sandbox(
            mode=self.permission_mode,
            workspace=self.workspace.root,
            runtime_dir=self.commands.runtime_dir,
            readable_roots=sandbox_readable_roots,
            writable_roots=sandbox_writable_roots,
            protected_paths=sandbox_protected_paths,
            network=self.allow_network,
        )
        self.sandbox_profile = build_sandbox_profile(
            mode=self.permission_mode,
            workspace=self.workspace.root,
            runtime_paths=sandbox_writable_roots,
            toolchain_paths=[str(path) for path in self.toolchain_read_roots],
            protected_paths=sandbox_protected_paths,
            network=self.allow_network,
            backend=self.process_sandbox.state,
        )
        enabled_features = frozenset({"view_image"}) if enable_view_image else frozenset()
        self.tool_dispatcher = ToolDispatcher(
            self.tool_registry,
            self,
            enabled_features=enabled_features,
        )
        self._tools = self.tool_dispatcher.definitions
        self.workflow_engine = WorkflowEngine(self)
        self.workflow_runs = WorkflowRunManager(
            self.workspace.root,
            engine=self.workflow_engine,
            registry=self.workflow_registry,
            approval_broker=LocalWorkflowApprovalBrokerClient.from_env(),
        )

    @property
    def local_permission_broker(self) -> Any | None:
        """Compatibility facade for tests/desktop integrations.

        The broker is owned by PermissionSession; callers that historically
        injected runtime.local_permission_broker keep working unchanged.
        """

        return self.permission_session.broker_client

    @local_permission_broker.setter
    def local_permission_broker(self, value: Any | None) -> None:
        self.permission_session.broker_client = value

    def close(self) -> None:
        self.commands.close()

    def _selected_toolchain_roots(self) -> list[Path]:
        roots: list[Path] = []
        toolchains = self._toolchain_snapshot.get("toolchains")
        if not isinstance(toolchains, dict):
            return roots
        seen: set[str] = set()
        for value in toolchains.values():
            if not isinstance(value, dict):
                continue
            selected = value.get("selected")
            if not isinstance(selected, dict):
                continue
            raw = str(selected.get("root") or "").strip()
            if not raw:
                continue
            try:
                root = Path(raw).resolve()
            except OSError:
                continue
            key = os.path.normcase(str(root))
            if key in seen or not root.exists():
                continue
            seen.add(key)
            roots.append(root)
        return roots

    @staticmethod
    def _platform_read_roots() -> list[Path]:
        if sys.platform == "darwin":
            return [
                Path("/System"),
                Path("/Library"),
                Path("/usr"),
                Path("/bin"),
                Path("/sbin"),
                Path("/private/etc"),
                Path("/private/var/db"),
                Path("/private/var/select"),
                Path("/opt/homebrew"),
                Path("/usr/local"),
            ]
        return []

    def _run_toolchain_probe(
        self,
        argv: list[str],
        env: Mapping[str, str],
        timeout: float,
        privileged: bool,
        readable_roots: Sequence[Path],
    ) -> subprocess.CompletedProcess[str]:
        permissions = (
            frozenset({"privileged_executable"}) if privileged else frozenset()
        )
        command = self.process_sandbox.wrap(
            argv,
            cwd=self.workspace.root,
            permissions=permissions,
            readable_roots=tuple(readable_roots),
        )
        return subprocess.run(
            command,
            cwd=str(self.workspace.root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            shell=False,
            env=dict(env),
        )

    def server_identity(self) -> dict[str, str]:
        return {"name": SERVER_NAME, "title": SERVER_TITLE, "version": __version__}

    def server_instructions(self) -> str:
        """Return MCP guidance with the latest user-authored Workflow catalog."""
        self._refresh_workspace_workflows()
        return self.project_context.server_instructions(self.workflow_registry.list())

    def auth_enabled(self) -> bool:
        return bool(self.auth_token or self.oauth_service)

    def list_tools(self) -> dict[str, Any]:
        return {
            "tools": [
                definition.mcp_definition(
                    fake_readonly=self.fake_readonly_annotations
                )
                for definition in self._tools
                if definition.mcp_exposed
            ]
        }

    def _permission_granted(self, permission: str) -> bool:
        return (
            permission in ACTIVE_PERMISSIONS.get()
            or self.permission_policy.operation_is_auto_granted(permission)
        )

    def _assert_tool_capabilities(self, name: str, capabilities: Any) -> None:
        missing = self.permission_policy.missing_capabilities(capabilities)
        if not missing:
            return
        raise RpcError(
            -32602,
            f"Tool is not available in permission profile: {name}",
            {
                "reason": "capability_denied",
                "missing_capabilities": sorted(
                    capability.value for capability in missing
                ),
            },
        )

    def _effective_permissions(
        self,
        name: str,
        arguments: dict[str, Any],
        context: RequestContext | None,
        round_granted: frozenset[str],
    ) -> frozenset[str]:
        stored = self.permission_session.stored_permissions_for_call(
            name,
            arguments,
            context,
        )
        session = self.permission_session.session_permissions_for_call(context)
        return frozenset(
            {
                *ACTIVE_PERMISSIONS.get(),
                *round_granted,
                *stored,
                *session,
            }
        )

    @staticmethod
    def _permission_target(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        target_tool = str(arguments.get("tool_name") or "")
        raw_arguments = arguments.get("arguments")
        target_arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
        return target_tool, target_arguments

    def _store_permission_result(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        permission: str,
        context: RequestContext | None,
        scope: str,
        ttl_seconds: int,
        constraint_scope: str | None = None,
        via: str | None = None,
    ) -> dict[str, Any]:
        target_tool, target_arguments = self._permission_target(arguments)
        grant_id, expires_at = self.permission_session.store_grant(
            tool_name=target_tool,
            arguments=target_arguments,
            permission=permission,
            principal=context.principal if context else "anonymous",
            scope=scope,
            ttl_seconds=ttl_seconds,
        )
        constraints = {
            "tool_name": target_tool,
            "arguments_hash": arguments_digest(
                target_tool,
                target_arguments,
            ),
            "permission": permission,
            "scope": constraint_scope or scope,
        }
        if via:
            constraints["via"] = via
        return make_tool_result(
            name,
            {
                "ok": True,
                "status": "granted",
                "grant_id": grant_id,
                "expires_at": expires_at,
                "constraints": constraints,
            },
        )

    def _handle_permission_required(
        self,
        name: str,
        arguments: dict[str, Any],
        exc: ToolError,
        *,
        context: RequestContext | None,
        granted: frozenset[str],
    ) -> dict[str, Any] | None:
        permission = str(exc.details.get("permission") or "")
        if (
            exc.code != "PERMISSION_REQUIRED"
            or not permission
            or permission in granted
        ):
            return None

        input_required = self.permission_session.input_required(
            name=name,
            arguments=arguments,
            permission=permission,
            message=exc.message,
            context=context,
            granted=granted,
        )
        if input_required is not None:
            return input_required

        decision = self.permission_session.request_local_permission(
            name=name,
            arguments=arguments,
            permission=permission,
            message=exc.message,
            context=context,
        )
        status = str(getattr(decision, "status", "unavailable"))
        session_scope = (
            status == "approved"
            and str(getattr(decision, "scope", "once")) == "session"
        )
        if status == "approved":
            if session_scope:
                self.permission_session.grant_session_permissions(context)
            if name == "request_permissions":
                scope = "session" if session_scope else str(arguments.get("scope") or "once")
                return self._store_permission_result(
                    name,
                    arguments,
                    permission=permission,
                    context=context,
                    scope=scope,
                    ttl_seconds=(
                        3_600
                        if session_scope
                        else int(arguments.get("ttl_seconds", 300))
                    ),
                    constraint_scope=("session_all" if session_scope else scope),
                    via="desktop_permission_broker",
                )
            retry_permissions = (
                ELICITABLE_PERMISSIONS
                if session_scope
                else frozenset({*granted, permission})
            )
            retry_token = ACTIVE_PERMISSIONS.set(retry_permissions)
            try:
                return self.call_tool(name, arguments, context=context)
            finally:
                ACTIVE_PERMISSIONS.reset(retry_token)

        if status == "denied":
            payload = {
                "ok": False,
                "error": {
                    "code": "PERMISSION_DENIED",
                    "message": "用户在 MicroMatrix Workbench 桌面端拒绝了本次授权。",
                    "category": "permission",
                    "retryable": False,
                    "details": {"permission": permission},
                },
            }
        elif name == "request_permissions":
            payload = {
                "ok": False,
                "status": "unsupported",
                "grant_id": None,
                "expires_at": None,
                "error": {
                    "code": "ELICITATION_UNSUPPORTED",
                    "message": "当前 MCP 客户端未声明可用的 elicitation form capability。",
                    "category": "permission",
                    "retryable": False,
                    "details": {"requested": arguments},
                },
            }
        else:
            payload = {"ok": False, "error": exc.payload()}
        return make_tool_result(name, payload)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        if context is None:
            context = current_request_context()
        definition, handler = self.tool_dispatcher.resolve(name, arguments)
        self._assert_tool_capabilities(name, definition.capabilities)
        image: tuple[str, str] | None = None
        round_granted, denied = self.permission_session.permission_round(
            name,
            arguments,
            context,
        )
        if denied:
            return make_tool_result(
                name,
                {
                    "ok": False,
                    "error": {
                        "code": "PERMISSION_DENIED",
                        "message": "用户拒绝或取消了本次临时授权。",
                        "category": "permission",
                        "retryable": False,
                        "details": {},
                    },
                },
            )
        granted = self._effective_permissions(
            name,
            arguments,
            context,
            round_granted,
        )
        if name == "request_permissions":
            requested_permission = str(arguments.get("permission") or "")
            if requested_permission in round_granted:
                scope = str(arguments.get("scope") or "once")
                return self._store_permission_result(
                    name,
                    arguments,
                    permission=requested_permission,
                    context=context,
                    scope=scope,
                    ttl_seconds=int(arguments.get("ttl_seconds", 300)),
                )
        permission_token = ACTIVE_PERMISSIONS.set(granted)
        request_context_token = ACTIVE_REQUEST_CONTEXT.set(context)
        try:
            try:
                payload = handler(arguments)
                payload.setdefault("ok", True)
                image_value = payload.pop("_image", None)
                if isinstance(image_value, tuple) and len(image_value) == 2:
                    image = (str(image_value[0]), str(image_value[1]))
            except ToolError as exc:
                handled = self._handle_permission_required(
                    name,
                    arguments,
                    exc,
                    context=context,
                    granted=granted,
                )
                if handled is not None:
                    return handled
                payload = {"ok": False, "error": exc.payload()}
            except Exception as exc:
                # Keep unexpected implementation failures inside the tool result
                # boundary. Otherwise the HTTP transport can be interrupted and
                # clients only see an opaque ExceptionGroup/TaskGroup failure.
                LOGGER.exception("Unexpected failure while calling MCP tool %s", name)
                payload = {
                    "ok": False,
                    "error": {
                        "code": "INTERNAL_TOOL_ERROR",
                        "message": "unexpected tool failure",
                        "category": "runtime",
                        "retryable": True,
                        "details": {"exception_type": type(exc).__name__},
                    },
                }
        finally:
            ACTIVE_REQUEST_CONTEXT.reset(request_context_token)
            ACTIVE_PERMISSIONS.reset(permission_token)
        return make_tool_result(name, payload, image=image)


"""Business runtime for the project-owned Coding Tools MCP tools."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .core import SERVER_NAME, SERVER_TITLE, ToolDispatcher
from .errors import RpcError, ToolError
from .processes import CommandManager
from .project_context import ProjectContext, load_project_context
from .protocol import RequestContext
from .permissions import (
    ACTIVE_PERMISSIONS,
    ELICITABLE_PERMISSIONS,
    PERMISSION_MODES,
    PermissionPolicy,
    PermissionSession,
    permission_profile,
)
from .results import make_tool_result
from .sandbox import build_sandbox_profile, create_process_sandbox
from .tools import build_tool_registry
from .tools.filesystem import FilesystemHandlers
from .tools.git import GitHandlers
from .tools.process import ProcessHandlers
from .tools.system import SystemHandlers
from .tools.toolchains import ToolchainHandlers
from .toolchains import ToolchainResolver
from .workspace import Workspace


LOGGER = logging.getLogger(__name__)


class Runtime(
    FilesystemHandlers,
    ProcessHandlers,
    GitHandlers,
    SystemHandlers,
    ToolchainHandlers,
):
    def __init__(
        self,
        workspace: Path,
        *,
        permission_mode: str = "safe",
        allow_network: bool = False,
        auth_token: str | None = None,
        oauth_config: Any = None,
        enable_view_image: bool = True,
        fake_readonly_annotations: bool = False,
        project_context: ProjectContext | None = None,
        permission_broker: Any | None = None,
        permission_broker_from_env: bool = True,
    ) -> None:
        if permission_mode not in PERMISSION_MODES:
            raise ValueError(f"unknown permission mode: {permission_mode}")
        if fake_readonly_annotations and permission_mode != "dangerous":
            raise ValueError("fake_readonly_annotations requires dangerous permission mode")
        self.workspace = Workspace(workspace)
        self.permission_mode = permission_mode
        self.permission_profile = permission_profile(permission_mode)
        self.permission_policy = PermissionPolicy(self.permission_profile)
        self.allow_network = allow_network or permission_mode in {"trusted", "dangerous"}
        self.auth_token = auth_token
        self.oauth_config = oauth_config
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
        self.tool_registry = build_tool_registry()
        enabled_features = frozenset({"view_image"}) if enable_view_image else frozenset()
        self.tool_dispatcher = ToolDispatcher(
            self.tool_registry,
            self,
            enabled_features=enabled_features,
        )
        self._tools = self.tool_dispatcher.definitions

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

    def auth_enabled(self) -> bool:
        return bool(self.auth_token or self.oauth_config)

    def list_tools(self) -> dict[str, Any]:
        return {
            "tools": [
                definition.mcp_definition(
                    fake_readonly=self.fake_readonly_annotations
                )
                for definition in self._tools
            ]
        }

    def _permission_granted(self, permission: str) -> bool:
        return (
            permission in ACTIVE_PERMISSIONS.get()
            or self.permission_policy.operation_is_auto_granted(permission)
        )

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        definition, handler = self.tool_dispatcher.resolve(name, arguments)
        missing_capabilities = self.permission_policy.missing_capabilities(
            definition.capabilities
        )
        if missing_capabilities:
            raise RpcError(
                -32602,
                f"Tool is not available in permission profile: {name}",
                {
                    "reason": "capability_denied",
                    "missing_capabilities": sorted(
                        capability.value for capability in missing_capabilities
                    ),
                },
            )
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
        stored_granted = self.permission_session.stored_permissions_for_call(
            name,
            arguments,
            context,
        )
        inherited_granted = ACTIVE_PERMISSIONS.get()
        session_granted = self.permission_session.session_permissions_for_call(context)
        granted = frozenset(
            {
                *inherited_granted,
                *round_granted,
                *stored_granted,
                *session_granted,
            }
        )
        if name == "request_permissions":
            requested_permission = str(arguments.get("permission") or "")
            if requested_permission in round_granted:
                target_tool = str(arguments.get("tool_name") or "")
                target_arguments_raw = arguments.get("arguments")
                target_arguments = (
                    target_arguments_raw
                    if isinstance(target_arguments_raw, dict)
                    else {}
                )
                grant_id, expires_at = self.permission_session.store_grant(
                    tool_name=target_tool,
                    arguments=target_arguments,
                    permission=requested_permission,
                    principal=context.principal if context else "anonymous",
                    scope=str(arguments.get("scope") or "once"),
                    ttl_seconds=int(arguments.get("ttl_seconds", 300)),
                )
                return make_tool_result(
                    name,
                    {
                        "ok": True,
                        "status": "granted",
                        "grant_id": grant_id,
                        "expires_at": expires_at,
                        "constraints": {
                            "tool_name": target_tool,
                            "arguments_hash": self.permission_session.arguments_digest(
                                target_tool,
                                target_arguments,
                            ),
                            "permission": requested_permission,
                            "scope": str(arguments.get("scope") or "once"),
                        },
                    },
                )
        permission_token = ACTIVE_PERMISSIONS.set(granted)
        try:
            try:
                payload = handler(arguments)
                payload.setdefault("ok", True)
                image_value = payload.pop("_image", None)
                if isinstance(image_value, tuple) and len(image_value) == 2:
                    image = (str(image_value[0]), str(image_value[1]))
            except ToolError as exc:
                permission = str(exc.details.get("permission") or "")
                if (
                    exc.code == "PERMISSION_REQUIRED"
                    and permission
                    and permission not in granted
                ):
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
                    local_decision = self.permission_session.request_local_permission(
                        name=name,
                        arguments=arguments,
                        permission=permission,
                        message=exc.message,
                        context=context,
                    )
                    local_status = str(getattr(local_decision, "status", "unavailable"))
                    local_session = (
                        local_status == "approved"
                        and str(getattr(local_decision, "scope", "once")) == "session"
                    )
                    if local_status == "approved":
                        if local_session:
                            self.permission_session.grant_session_permissions(context)
                        if name == "request_permissions":
                            target_tool = str(arguments.get("tool_name") or "")
                            target_arguments_raw = arguments.get("arguments")
                            target_arguments = (
                                target_arguments_raw
                                if isinstance(target_arguments_raw, dict)
                                else {}
                            )
                            grant_id, expires_at = self.permission_session.store_grant(
                                tool_name=target_tool,
                                arguments=target_arguments,
                                permission=permission,
                                principal=context.principal if context else "anonymous",
                                scope=(
                                    "session"
                                    if local_session
                                    else str(arguments.get("scope") or "once")
                                ),
                                ttl_seconds=(
                                    3_600
                                    if local_session
                                    else int(arguments.get("ttl_seconds", 300))
                                ),
                            )
                            return make_tool_result(
                                name,
                                {
                                    "ok": True,
                                    "status": "granted",
                                    "grant_id": grant_id,
                                    "expires_at": expires_at,
                                    "constraints": {
                                        "tool_name": target_tool,
                                        "arguments_hash": self.permission_session.arguments_digest(
                                            target_tool,
                                            target_arguments,
                                        ),
                                        "permission": permission,
                                        "scope": (
                                            "session_all"
                                            if local_session
                                            else str(arguments.get("scope") or "once")
                                        ),
                                        "via": "desktop_permission_broker",
                                    },
                                },
                            )
                        retry_permissions = (
                            ELICITABLE_PERMISSIONS
                            if local_session
                            else frozenset({*granted, permission})
                        )
                        retry_token = ACTIVE_PERMISSIONS.set(retry_permissions)
                        try:
                            return self.call_tool(name, arguments, context=context)
                        finally:
                            ACTIVE_PERMISSIONS.reset(retry_token)
                    if local_status == "denied":
                        payload = {
                            "ok": False,
                            "error": {
                                "code": "PERMISSION_DENIED",
                                "message": "用户在 Coding Tools MCP 桌面端拒绝了本次授权。",
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
                else:
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
            ACTIVE_PERMISSIONS.reset(permission_token)
        return make_tool_result(name, payload, image=image)



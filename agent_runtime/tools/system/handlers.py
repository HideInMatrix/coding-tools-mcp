from __future__ import annotations

import os
from typing import Any

from ... import __version__
from ...core import ENDPOINT_PATH, SERVER_NAME, SERVER_TITLE
from ...errors import ToolError
from ...permissions import ELICITABLE_PERMISSIONS
from ...processes import STREAM_HEAD_BYTES, STREAM_LIMIT_BYTES
from ...protocol import KNOWN_PROTOCOL_VERSIONS


class SystemHandlers:
    """Server introspection, environment diagnostics and permission tool handlers."""

    def server_info(self, _args: dict[str, Any]) -> dict[str, Any]:
        tools = [definition.name for definition in self._tools]
        return {
            "server": SERVER_NAME,
            "title": SERVER_TITLE,
            "version": __version__,
            "workspace": str(self.workspace.root),
            "permission_mode": self.permission_mode,
            "permission_profile": {
                "name": self.permission_profile.name,
                "capabilities": sorted(
                    capability.value
                    for capability in self.permission_profile.capabilities
                ),
                "auto_granted_operations": sorted(
                    permission.value
                    for permission in self.permission_profile.auto_granted_operations
                ),
            },
            "permission_session": {
                "scope": "runtime_profile",
                "principal_isolated": True,
                "request_state_single_use": True,
                "grant_argument_bound": True,
            },
            "auth_enabled": self.auth_enabled(),
            "supported_protocol_versions": list(KNOWN_PROTOCOL_VERSIONS),
            "endpoint_path": ENDPOINT_PATH,
            "runtime_dir": str(self.commands.runtime_dir),
            "home": str(self.commands.home_dir),
            "tmpdir": str(self.commands.tmp_dir),
            "cache_dir": str(self.commands.cache_dir),
            "network_allowed": self.allow_network,
            "dangerously_skip_all_permissions": self.permission_mode == "dangerous",
            "annotation_override": (
                "fake_readonly" if self.fake_readonly_annotations else None
            ),
            "landlock": {
                "available": False,
                "enabled": False,
                "abi_version": None,
                "reason": "Landlock compatibility field; process isolation is reported in sandbox.",
                "details": {},
            },
            "sandbox": self.sandbox_profile.to_dict(),
            "exec_policy": {
                "shell_expansion": (
                    "allowed"
                    if self.permission_mode == "dangerous"
                    else "restricted"
                    if self.permission_mode == "trusted"
                    else "blocked"
                ),
                "inline_script": (
                    "allowed" if self.permission_mode != "safe" else "blocked"
                ),
                "secret_env_filter": self.permission_mode != "dangerous",
                "global_tmp_write": (
                    "allowed" if self.permission_mode == "dangerous" else "blocked"
                ),
            },
            "shell_env_inherit": (
                "sanitized" if self.permission_mode != "dangerous" else "full"
            ),
            "shell_env_include_only": (
                [
                    "PATH",
                    "LANG",
                    "LC_ALL",
                    "TERM",
                    "PATHEXT",
                    "COMSPEC",
                    "SYSTEMROOT",
                    "WINDIR",
                    "PROGRAMDATA",
                    "PROGRAMFILES",
                    "PROGRAMFILES(X86)",
                    "PROGRAMW6432",
                ]
                if self.permission_mode != "dangerous"
                else []
            ),
            "shell_env_exclude": [],
            "safe_exec_path": list(self.safe_exec_path),
            "toolchains": self._toolchain_snapshot.get("toolchains", {}),
            "output_retention": {
                "buffer_bytes_per_stream": STREAM_LIMIT_BYTES,
                "head_bytes_per_stream": STREAM_HEAD_BYTES,
            },
            "project_context": {
                "root_instruction_files": [
                    item.path for item in self.project_context.root_files
                ],
                "nested_instruction_files": list(self.project_context.nested_files),
                "warnings": list(self.project_context.warnings),
            },
            "tools": tools,
            "tool_capabilities": {
                definition.name: sorted(
                    capability.value for capability in definition.capabilities
                )
                for definition in self._tools
            },
            "tool_count": len(tools),
        }

    def check_exec_environment(self, _args: dict[str, Any]) -> dict[str, Any]:
        warnings = []
        if not self.sandbox_profile.os_kernel_sandbox:
            warnings.append(
                "OS-kernel process confinement is not enforced yet; capability policy, workspace guards, sanitized environment, and offline hints provide defense in depth."
            )
            if self.sandbox_profile.backend_reason:
                warnings.append(self.sandbox_profile.backend_reason)
        else:
            if not self.sandbox_profile.filesystem_isolation:
                warnings.append(
                    "OS process isolation is enabled, but filesystem confinement is not enforced by the active backend."
                )
            if not self.sandbox_profile.network_isolation:
                warnings.append(
                    "OS process isolation is enabled, but network confinement is not enforced by the active backend."
                )
        if self.permission_mode == "dangerous":
            warnings.append("permission_mode=dangerous disables MCP safety gates")
        return {
            "workspace": str(self.workspace.root),
            "permission_mode": self.permission_mode,
            "network_allowed": self.allow_network,
            "runtime_dir": str(self.commands.runtime_dir),
            "home": str(self.commands.home_dir),
            "tmpdir": str(self.commands.tmp_dir),
            "cache_dir": str(self.commands.cache_dir),
            "landlock_enabled": False,
            "landlock_abi": None,
            "global_tmp_write": (
                "allowed" if self.permission_mode == "dangerous" else "blocked"
            ),
            "effective_path": (
                list(self.safe_exec_path)
                if self.permission_mode != "dangerous"
                else os.environ.get("PATH", "").split(os.pathsep)
            ),
            "toolchains": self._toolchain_snapshot.get("toolchains", {}),
            "warnings": warnings,
            "sandbox": self.sandbox_profile.to_dict(),
        }

    def request_permissions(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.permission_mode == "dangerous":
            return {
                "ok": True,
                "status": "granted",
                "grant_id": "dangerously-skip-all-permissions",
                "expires_at": None,
                "constraints": {
                    "mode": "dangerously_skip_all_permissions",
                    "workspace": str(self.workspace.root),
                    "requested": args,
                },
                "warnings": [
                    "permission_mode=dangerous is enabled; permission-gated operations are auto-granted"
                ],
            }
        permission = str(args.get("permission") or "")
        if permission not in ELICITABLE_PERMISSIONS:
            return {
                "ok": False,
                "status": "unsupported",
                "grant_id": None,
                "expires_at": None,
                "error": {
                    "code": "PERMISSION_NOT_ELICITABLE",
                    "message": "该权限不能通过临时用户授权提升，请修改 Server 权限模式或配置。",
                    "category": "permission",
                    "retryable": False,
                    "details": {"permission": permission},
                },
            }
        raise ToolError(
            "PERMISSION_REQUIRED",
            str(args.get("reason") or "该操作需要用户临时授权。"),
            "permission",
            False,
            {"permission": permission, "requested": dict(args)},
        )

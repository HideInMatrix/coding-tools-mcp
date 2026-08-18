"""Business runtime for the project-owned Coding Tools MCP tools."""

from __future__ import annotations

import base64
import contextvars
import fnmatch
import hashlib
import hmac
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import __compatibility_baseline__, __version__
from .errors import RpcError, ToolError
from .local_permission_broker import (
    BROKER_DIR_ENV,
    BROKER_SECRET_ENV,
    BROKER_SERVER_ID_ENV,
    LocalPermissionBrokerClient,
    redact_for_display,
)
from .patching import apply_patch as apply_patch_envelope
from .processes import (
    STREAM_HEAD_BYTES,
    STREAM_LIMIT_BYTES,
    CommandManager,
    command_payload,
)
from .project_context import ProjectContext, load_project_context
from .protocol import KNOWN_PROTOCOL_VERSIONS, RequestContext
from .results import make_tool_result
from .sandbox import build_sandbox_profile, create_process_sandbox
from .schemas import TOOL_SPECS, exposed_specs, validate_value
from .toolchains import ToolchainResolver
from .workspace import Workspace, matches_any


LOGGER = logging.getLogger(__name__)


SERVER_NAME = "coding-tools-mcp"
SERVER_TITLE = "Coding Tools MCP"
ENDPOINT_PATH = "/mcp"
PERMISSION_MODES = ("safe", "trusted", "dangerous")

SENSITIVE_ENV_RE = re.compile(r"(token|secret|credential|api[_-]?key|password|passwd|private)", re.I)
SANDBOX_PROTECTED_ENV = {
    "HOME",
    "PATH",
    "TMPDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "GOPROXY",
    "GOTOOLCHAIN",
    "PIP_NO_INDEX",
    "npm_config_offline",
    "YARN_ENABLE_NETWORK",
    "CARGO_NET_OFFLINE",
}
NETWORK_RE = re.compile(r"(https?://|\bcurl\b|\bwget\b|\bssh\b|\bscp\b|\bftp\b|\bnc\b|\bnetcat\b|socket\.|requests\.|urllib\.|httpx\b|aiohttp\b)", re.I)
NETWORK_COMMAND_RE = re.compile(
    r"(?:\b(?:npm|pnpm|yarn)\s+(?:install|i|ci|add|update|upgrade|publish|audit|outdated)\b|"
    r"\b(?:pip|pip3)\s+install\b|\bpython(?:3)?\s+-m\s+pip\s+install\b|"
    r"\bgo\s+(?:get|install)\b|\bgo\s+mod\s+download\b|"
    r"\bgit\s+(?:clone|fetch|pull|push|ls-remote)\b|"
    r"\bcargo\s+(?:fetch|install|update|publish|search)\b)",
    re.I,
)
GIT_METADATA_WRITE_RE = re.compile(
    r"\bgit\s+(?:add|commit|merge|rebase|cherry-pick|revert|checkout|switch|stash|tag|update-index|write-tree|reset|clean)\b",
    re.I,
)
WINDOWS_BATCH_META_RE = re.compile(r"[&|<>^()%!\r\n\"]")
SHELL_EXPANSION_RE = re.compile(r"(`|\$\(|\$\{|\$[A-Za-z_][A-Za-z0-9_]*)")
INLINE_SCRIPT_RE = re.compile(r"\b(python(?:3)?\s+-c|node\s+-e|ruby\s+-e|perl\s+-e|(?:ba|z|)sh\s+-c)\b", re.I)
DESTRUCTIVE_RE = re.compile(r"(^|\s)(sudo\b|su\b|mkfs\b|mount\b|umount\b|chmod\s+-R\b|chown\s+-R\b|rm\s+-[^\s]*r[^\s]*f\b|rm\s+-[^\s]*f[^\s]*r\b)", re.I)
REDIRECT_ESCAPE_RE = re.compile(r"(?:^|\s)(?:>|>>|<)\s*(/[^\s]+|\.\./[^\s]+)")
ELICITABLE_PERMISSIONS = frozenset(
    {
        "network",
        "destructive_command",
        "git_metadata_write",
        "long_timeout",
        "sensitive_env",
        "shell_expansion",
        "inline_script",
        "privileged_executable",
    }
)
PERMISSION_STATE_TTL_SECONDS = 300
ACTIVE_PERMISSIONS: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "coding_tools_mcp_active_permissions",
    default=frozenset(),
)


def _truncate_text(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= max_bytes:
        return text, False
    return raw[:max_bytes].decode("utf-8", "ignore"), True


def _iso_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
    except OSError:
        return ""


def _parse_git_branch_line(line: str) -> tuple[str, str, int, int]:
    branch = line
    upstream = ""
    ahead = 0
    behind = 0
    if "..." in line:
        branch, rest = line.split("...", 1)
        upstream = rest.split(" ", 1)[0]
    if "[" in line and "]" in line:
        meta = line.split("[", 1)[1].split("]", 1)[0]
        ahead_match = re.search(r"ahead (\d+)", meta)
        behind_match = re.search(r"behind (\d+)", meta)
        ahead = int(ahead_match.group(1)) if ahead_match else 0
        behind = int(behind_match.group(1)) if behind_match else 0
    return branch.strip(), upstream.strip(), ahead, behind


def _parse_diff_files(diff_text: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                current = {"path": path, "status": "modified", "binary": False}
                files.append(current)
        elif current is not None and line.startswith("new file mode"):
            current["status"] = "added"
        elif current is not None and line.startswith("deleted file mode"):
            current["status"] = "deleted"
        elif current is not None and line.startswith("Binary files"):
            current["binary"] = True
    return files


class Runtime:
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
    ) -> None:
        if permission_mode not in PERMISSION_MODES:
            raise ValueError(f"unknown permission mode: {permission_mode}")
        if fake_readonly_annotations and permission_mode != "dangerous":
            raise ValueError("fake_readonly_annotations requires dangerous permission mode")
        self.workspace = Workspace(workspace)
        self.permission_mode = permission_mode
        self.allow_network = allow_network or permission_mode in {"trusted", "dangerous"}
        self.auth_token = auth_token
        self.oauth_config = oauth_config
        self.enable_view_image = enable_view_image
        self.fake_readonly_annotations = fake_readonly_annotations
        self.project_context = project_context or load_project_context(self.workspace.root)
        self.commands = CommandManager(self.workspace.root)
        self.local_permission_broker = LocalPermissionBrokerClient.from_env()
        self._permission_state_secret = secrets.token_bytes(32)
        self._permission_state_lock = threading.RLock()
        self._consumed_permission_states: dict[str, float] = {}
        self._permission_grants_lock = threading.RLock()
        self._permission_grants: dict[str, dict[str, Any]] = {}
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
        self._specs = exposed_specs(enable_view_image=enable_view_image)
        self._spec_map = {spec.name: spec for spec in self._specs}

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
                spec.definition(fake_readonly=self.fake_readonly_annotations)
                for spec in self._specs
            ]
        }

    @staticmethod
    def _permission_granted(permission: str) -> bool:
        return permission in ACTIVE_PERMISSIONS.get()

    @staticmethod
    def _arguments_digest(name: str, arguments: dict[str, Any]) -> str:
        encoded = json.dumps(
            {"tool": name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _b64url_encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64url_decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    def _mint_permission_state(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        permission: str,
        principal: str,
        granted: frozenset[str],
    ) -> str:
        payload = {
            "v": 1,
            "tool": name,
            "arguments_hash": self._arguments_digest(name, arguments),
            "permission": permission,
            "granted": sorted(granted),
            "workspace": str(self.workspace.root),
            "principal": principal or "anonymous",
            "exp": int(time.time()) + PERMISSION_STATE_TTL_SECONDS,
            "nonce": secrets.token_urlsafe(12),
        }
        encoded = self._b64url_encode(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signature = self._b64url_encode(
            hmac.new(
                self._permission_state_secret,
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        return f"ctpg1.{encoded}.{signature}"

    def _verify_permission_state(
        self,
        state: str,
        *,
        name: str,
        arguments: dict[str, Any],
        principal: str,
    ) -> dict[str, Any]:
        try:
            prefix, encoded, signature = state.split(".", 2)
            if prefix != "ctpg1":
                raise ValueError("unknown state prefix")
            expected = self._b64url_encode(
                hmac.new(
                    self._permission_state_secret,
                    encoded.encode("ascii"),
                    hashlib.sha256,
                ).digest()
            )
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid state signature")
            raw = json.loads(self._b64url_decode(encoded).decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("state payload must be an object")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RpcError(
                -32602,
                "Invalid permission requestState",
                {"reason": "permission_state_invalid"},
            ) from exc

        now = int(time.time())
        if int(raw.get("exp", 0)) < now:
            raise RpcError(
                -32602,
                "Permission requestState has expired",
                {"reason": "permission_state_expired"},
            )
        if raw.get("tool") != name or raw.get("arguments_hash") != self._arguments_digest(name, arguments):
            raise RpcError(
                -32602,
                "Permission requestState does not match this tool call",
                {"reason": "permission_state_binding"},
            )
        if raw.get("workspace") != str(self.workspace.root):
            raise RpcError(
                -32602,
                "Permission requestState does not match this workspace",
                {"reason": "permission_state_workspace"},
            )
        if raw.get("principal") != (principal or "anonymous"):
            raise RpcError(
                -32602,
                "Permission requestState does not match the authenticated principal",
                {"reason": "permission_state_principal"},
            )
        permission = raw.get("permission")
        if not isinstance(permission, str) or permission not in ELICITABLE_PERMISSIONS:
            raise RpcError(
                -32602,
                "Permission requestState contains an unsupported permission",
                {"reason": "permission_state_permission"},
            )
        return raw

    def _consume_permission_state(self, state: str, expires_at: int) -> None:
        state_id = hashlib.sha256(state.encode("utf-8")).hexdigest()
        now = time.time()
        with self._permission_state_lock:
            expired = [
                key
                for key, expiry in self._consumed_permission_states.items()
                if expiry <= now
            ]
            for key in expired:
                self._consumed_permission_states.pop(key, None)
            if state_id in self._consumed_permission_states:
                raise RpcError(
                    -32602,
                    "Permission requestState has already been consumed",
                    {"reason": "permission_state_replay"},
                )
            self._consumed_permission_states[state_id] = float(expires_at)

    def _store_permission_grant(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        permission: str,
        principal: str,
        scope: str,
        ttl_seconds: int,
    ) -> tuple[str, int]:
        now = int(time.time())
        expires_at = now + max(1, min(int(ttl_seconds), 3_600))
        grant_id = f"ctg_{secrets.token_urlsafe(18)}"
        record = {
            "tool_name": tool_name,
            "arguments_hash": self._arguments_digest(tool_name, arguments),
            "permission": permission,
            "principal": principal or "anonymous",
            "scope": "session" if scope == "session" else "once",
            "expires_at": expires_at,
        }
        with self._permission_grants_lock:
            self._permission_grants[grant_id] = record
        return grant_id, expires_at

    def _stored_permissions_for_call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: RequestContext | None,
    ) -> frozenset[str]:
        if context is None:
            return frozenset()
        now = int(time.time())
        principal = context.principal or "anonymous"
        arguments_hash = self._arguments_digest(name, arguments)
        granted: set[str] = set()
        consume: list[str] = []
        with self._permission_grants_lock:
            for grant_id, record in list(self._permission_grants.items()):
                if int(record.get("expires_at", 0)) < now:
                    self._permission_grants.pop(grant_id, None)
                    continue
                if (
                    record.get("tool_name") != name
                    or record.get("arguments_hash") != arguments_hash
                    or record.get("principal") != principal
                ):
                    continue
                permission = record.get("permission")
                if isinstance(permission, str) and permission in ELICITABLE_PERMISSIONS:
                    granted.add(permission)
                    if record.get("scope") == "once":
                        consume.append(grant_id)
            for grant_id in consume:
                self._permission_grants.pop(grant_id, None)
        return frozenset(granted)

    def _permission_round(
        self,
        name: str,
        arguments: dict[str, Any],
        context: RequestContext | None,
    ) -> tuple[frozenset[str], bool]:
        if context is None or context.request_state is None:
            if context and context.input_responses and "permission" in context.input_responses:
                raise RpcError(
                    -32602,
                    "Permission inputResponses require a matching requestState",
                    {"reason": "permission_response_without_state"},
                )
            return frozenset(), False

        state = self._verify_permission_state(
            context.request_state,
            name=name,
            arguments=arguments,
            principal=context.principal,
        )
        responses = context.input_responses or {}
        response = responses.get("permission")
        if not isinstance(response, dict):
            raise RpcError(
                -32602,
                "Permission requestState requires inputResponses.permission",
                {"reason": "permission_response_missing"},
            )
        self._consume_permission_state(
            context.request_state,
            int(state.get("exp", 0)),
        )
        raw_granted = state.get("granted")
        granted = {
            str(item)
            for item in raw_granted
            if isinstance(raw_granted, list) and isinstance(item, str)
        } if isinstance(raw_granted, list) else set()
        action = response.get("action")
        content = response.get("content")
        confirmed = isinstance(content, dict) and content.get("confirm") is True
        if action != "accept" or not confirmed:
            return frozenset(granted), True
        granted.add(str(state["permission"]))
        return frozenset(granted), False

    @staticmethod
    def _supports_permission_elicitation(context: RequestContext | None) -> bool:
        if context is None or context.era != "modern":
            return False
        capabilities = context.client_capabilities
        if not isinstance(capabilities, Mapping):
            return False
        elicitation = capabilities.get("elicitation")
        if not isinstance(elicitation, Mapping):
            return False
        if not elicitation:
            return True
        return isinstance(elicitation.get("form"), Mapping)

    @staticmethod
    def _permission_message(
        permission: str,
        name: str,
        arguments: dict[str, Any],
        fallback: str,
    ) -> str:
        descriptions = {
            "network": "该操作需要访问网络。",
            "destructive_command": "该操作包含潜在破坏性的 Workspace 命令。",
            "git_metadata_write": "该操作需要写入当前 Workspace 的 .git 元数据。",
            "long_timeout": "该操作需要超过 Safe 模式默认上限的执行时间。",
            "sensitive_env": "该操作需要向子进程传入敏感环境变量。",
            "shell_expansion": "该操作需要启用受限制的 Shell 展开能力。",
            "inline_script": "该操作需要执行内联脚本。",
            "privileged_executable": "沙箱 PATH 中未找到所需工具，需要读取用户工具环境并扩大只读执行范围后重试。",
        }
        try:
            rendered = json.dumps(
                redact_for_display(arguments),
                ensure_ascii=False,
                sort_keys=True,
            )
        except (TypeError, ValueError):
            rendered = str(arguments)
        if len(rendered) > 700:
            rendered = rendered[:697] + "..."
        return (
            f"{descriptions.get(permission, fallback)}\n"
            f"工具：{name}\n"
            f"参数：{rendered}\n"
            "仅授权这一次完全相同的工具调用，是否允许？"
        )

    def _permission_input_required(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        permission: str,
        message: str,
        context: RequestContext | None,
        granted: frozenset[str],
    ) -> dict[str, Any] | None:
        if permission not in ELICITABLE_PERMISSIONS:
            return None
        if not self._supports_permission_elicitation(context):
            return None
        assert context is not None
        return {
            "resultType": "input_required",
            "inputRequests": {
                "permission": {
                    "method": "elicitation/create",
                    "params": {
                        "mode": "form",
                        "message": self._permission_message(
                            permission,
                            name,
                            arguments,
                            message,
                        ),
                        "requestedSchema": {
                            "type": "object",
                            "properties": {
                                "confirm": {
                                    "type": "boolean",
                                    "title": "允许本次操作",
                                    "description": "仅授权当前完全相同的工具调用。",
                                    "default": False,
                                }
                            },
                            "required": ["confirm"],
                        },
                    },
                }
            },
            "requestState": self._mint_permission_state(
                name=name,
                arguments=arguments,
                permission=permission,
                principal=context.principal,
                granted=granted,
            ),
        }

    def _request_local_permission(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        permission: str,
        message: str,
        context: RequestContext | None,
    ) -> str:
        broker = self.local_permission_broker
        if broker is None or permission not in ELICITABLE_PERMISSIONS:
            return "unavailable"
        return broker.request(
            tool_name=name,
            arguments=arguments,
            permission=permission,
            reason=message,
            principal=context.principal if context else "anonymous",
        ).status

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        spec = self._spec_map.get(name)
        if spec is None:
            raise RpcError(-32602, f"Unknown tool: {name}", {"reason": "unknown_tool"})
        try:
            validate_value(arguments, spec.input_schema)
        except ValueError as exc:
            raise RpcError(-32602, str(exc), {"reason": "invalid_arguments"}) from exc
        handler = getattr(self, name)
        image: tuple[str, str] | None = None
        round_granted, denied = self._permission_round(name, arguments, context)
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
        stored_granted = self._stored_permissions_for_call(name, arguments, context)
        granted = frozenset({*round_granted, *stored_granted})
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
                grant_id, expires_at = self._store_permission_grant(
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
                            "arguments_hash": self._arguments_digest(
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
                    input_required = self._permission_input_required(
                        name=name,
                        arguments=arguments,
                        permission=permission,
                        message=exc.message,
                        context=context,
                        granted=granted,
                    )
                    if input_required is not None:
                        return input_required
                    local_status = self._request_local_permission(
                        name=name,
                        arguments=arguments,
                        permission=permission,
                        message=exc.message,
                        context=context,
                    )
                    if local_status == "approved":
                        if name == "request_permissions":
                            target_tool = str(arguments.get("tool_name") or "")
                            target_arguments_raw = arguments.get("arguments")
                            target_arguments = (
                                target_arguments_raw
                                if isinstance(target_arguments_raw, dict)
                                else {}
                            )
                            grant_id, expires_at = self._store_permission_grant(
                                tool_name=target_tool,
                                arguments=target_arguments,
                                permission=permission,
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
                                        "arguments_hash": self._arguments_digest(
                                            target_tool,
                                            target_arguments,
                                        ),
                                        "permission": permission,
                                        "scope": str(arguments.get("scope") or "once"),
                                        "via": "desktop_permission_broker",
                                    },
                                },
                            )
                        self._store_permission_grant(
                            tool_name=name,
                            arguments=arguments,
                            permission=permission,
                            principal=context.principal if context else "anonymous",
                            scope="once",
                            ttl_seconds=60,
                        )
                        return self.call_tool(name, arguments, context=context)
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

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def server_info(self, _args: dict[str, Any]) -> dict[str, Any]:
        tools = [spec.name for spec in self._specs]
        return {
            "server": SERVER_NAME,
            "title": SERVER_TITLE,
            "version": __version__,
            "compatibility_baseline": __compatibility_baseline__,
            "workspace": str(self.workspace.root),
            "permission_mode": self.permission_mode,
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
                "inline_script": "allowed" if self.permission_mode != "safe" else "blocked",
                "secret_env_filter": self.permission_mode != "dangerous",
                "global_tmp_write": "allowed" if self.permission_mode == "dangerous" else "blocked",
            },
            "shell_env_inherit": "sanitized" if self.permission_mode != "dangerous" else "full",
            "shell_env_include_only": ["PATH", "LANG", "LC_ALL", "TERM", "PATHEXT", "COMSPEC", "SYSTEMROOT", "WINDIR", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"] if self.permission_mode != "dangerous" else [],
            "shell_env_exclude": [],
            "safe_exec_path": list(self.safe_exec_path),
            "toolchains": self._toolchain_snapshot.get("toolchains", {}),
            "output_retention": {
                "buffer_bytes_per_stream": STREAM_LIMIT_BYTES,
                "head_bytes_per_stream": STREAM_HEAD_BYTES,
            },
            "project_context": {
                "root_instruction_files": [item.path for item in self.project_context.root_files],
                "nested_instruction_files": list(self.project_context.nested_files),
                "warnings": list(self.project_context.warnings),
            },
            "tools": tools,
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
            "global_tmp_write": "allowed" if self.permission_mode == "dangerous" else "blocked",
            "effective_path": list(self.safe_exec_path) if self.permission_mode != "dangerous" else os.environ.get("PATH", "").split(os.pathsep),
            "toolchains": self._toolchain_snapshot.get("toolchains", {}),
            "warnings": warnings,
            "sandbox": self.sandbox_profile.to_dict(),
        }

    def discover_toolchains(self, args: dict[str, Any]) -> dict[str, Any]:
        kinds = [str(item) for item in list(args.get("kinds") or ["node", "python", "go"])]
        privileged = self.permission_mode == "dangerous" or self._permission_granted(
            "privileged_executable"
        )
        discovered = self.toolchains.discover(kinds, privileged=privileged)
        toolchains = discovered.get("toolchains")
        missing = [
            kind
            for kind in kinds
            if not isinstance(toolchains, dict)
            or not isinstance(toolchains.get(kind), dict)
            or toolchains[kind].get("selected") is None
        ]
        if missing and not privileged:
            raise ToolError(
                "PERMISSION_REQUIRED",
                "沙箱环境中未找到请求的工具链；需要读取用户登录环境后重试。",
                "permission",
                False,
                {
                    "permission": "privileged_executable",
                    "missing": missing,
                    "sandbox_path": list(self.safe_exec_path),
                },
            )
        return {
            **discovered,
            "shell_startup_files_evaluated": privileged and os.name != "nt",
            "home_scanned_recursively": False,
            "elevated_user_environment_queried": privileged,
        }

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------
    def read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.workspace.existing(str(args["path"]))
        if resolved.absolute.is_dir():
            raise ToolError("IS_DIRECTORY", "path is a directory", "validation")
        if not resolved.absolute.is_file():
            raise ToolError("NOT_FILE", f"not a file: {resolved.display}", "filesystem")
        max_bytes = int(args.get("max_bytes", 131_072))
        start = int(args.get("start_line", 1))
        requested_end = args.get("end_line")
        requested_max_lines = args.get("max_lines")
        if requested_end is not None and requested_max_lines is not None:
            calculated_end = start + int(requested_max_lines) - 1
            if int(requested_end) != calculated_end:
                raise ToolError(
                    "INVALID_ARGUMENT",
                    "end_line and max_lines select different ranges",
                    "validation",
                )
        end = (
            int(requested_end)
            if requested_end is not None
            else start + int(requested_max_lines) - 1
            if requested_max_lines is not None
            else None
        )
        if end is not None and end < start:
            raise ToolError(
                "INVALID_RANGE",
                "end_line must be greater than or equal to start_line",
                "validation",
            )
        try:
            total_bytes = resolved.absolute.stat().st_size
            with resolved.absolute.open("rb") as raw_handle:
                if b"\x00" in raw_handle.read(4096):
                    raise ToolError(
                        "BINARY_FILE",
                        f"binary file read blocked for text tool: {resolved.display}",
                        "validation",
                    )
        except ToolError:
            raise
        except OSError as exc:
            raise ToolError(
                "READ_FAILED",
                f"cannot read file: {resolved.display}",
                "filesystem",
                True,
                {"error": str(exc)},
            ) from exc

        selected_parts: list[str] = []
        selected_bytes = 0
        total_lines = 0
        byte_limit_hit = False
        try:
            with resolved.absolute.open(
                "r",
                encoding="utf-8",
                errors="strict",
                newline="",
            ) as handle:
                for total_lines, line in enumerate(handle, start=1):
                    if total_lines < start:
                        continue
                    if end is not None and total_lines > end:
                        continue
                    if byte_limit_hit:
                        continue
                    encoded = line.encode("utf-8")
                    remaining = max_bytes - selected_bytes
                    if len(encoded) <= remaining:
                        selected_parts.append(line)
                        selected_bytes += len(encoded)
                        continue
                    if remaining > 0:
                        selected_parts.append(
                            encoded[:remaining].decode("utf-8", "ignore")
                        )
                        selected_bytes = max_bytes
                    byte_limit_hit = True
        except UnicodeDecodeError as exc:
            raise ToolError(
                "UNSUPPORTED_ENCODING",
                f"file is not valid UTF-8: {resolved.display}",
                "validation",
            ) from exc
        except OSError as exc:
            raise ToolError("READ_FAILED", f"cannot read file: {resolved.display}", "filesystem", True, {"error": str(exc)}) from exc

        content = "".join(selected_parts)
        actual_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        selected_end = min(end, total_lines) if end is not None else total_lines
        actual_end = min(selected_end, start + max(0, actual_lines - 1)) if content else start - 1
        range_has_more = selected_end < total_lines
        truncated = byte_limit_hit or range_has_more
        next_start_line = actual_end + 1 if truncated and actual_end < total_lines else None
        return {
            "path": resolved.display,
            "content": content,
            "encoding": "utf-8",
            "max_bytes": max_bytes,
            "start_line": start,
            "end_line": actual_end,
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "bytes_read": len(content.encode("utf-8")),
            "truncated": truncated,
            "truncated_by": "bytes" if byte_limit_hit else "lines" if range_has_more else None,
            "next_start_line": next_start_line,
            "warnings": ["content truncated"] if truncated else [],
        }

    def list_dir(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.workspace.existing(str(args.get("path", ".")))
        if not resolved.absolute.is_dir():
            raise ToolError("NOT_A_DIRECTORY", f"not a directory: {resolved.display}", "filesystem")
        recursive = bool(args.get("recursive", False))
        max_depth = int(args.get("max_depth", 1))
        max_entries = int(args.get("max_entries", 1_000))
        include_hidden = bool(args.get("include_hidden", False))
        include_ignored = bool(args.get("include_ignored", False))
        entries: list[dict[str, Any]] = []
        base_depth = len(resolved.absolute.parts)
        stack = [resolved.absolute]
        while stack and len(entries) < max_entries:
            directory = stack.pop()
            try:
                children = list(directory.iterdir())
            except OSError as exc:
                raise ToolError("LIST_FAILED", f"cannot list directory: {directory}", "filesystem", True, {"error": str(exc)}) from exc
            for child in children:
                relative = child.relative_to(self.workspace.root)
                if not include_hidden and self.workspace.hidden(relative):
                    continue
                if not include_ignored and self.workspace.ignored(relative):
                    continue
                try:
                    stat = child.lstat()
                    kind = "symlink" if child.is_symlink() else "directory" if child.is_dir() else "file"
                    size = stat.st_size
                except OSError:
                    kind, size = "unknown", 0
                entries.append({"name": child.name, "path": relative.as_posix(), "type": kind, "size_bytes": size, "modified": _iso_mtime(child)})
                if len(entries) >= max_entries:
                    break
                depth = len(child.parts) - base_depth
                if recursive and kind == "directory" and depth < max_depth:
                    stack.append(child)
        sort = str(args.get("sort", "name"))
        if sort == "type":
            entries.sort(key=lambda item: (item["type"], item["name"]))
        elif sort == "modified":
            entries.sort(key=lambda item: item["modified"])
        else:
            entries.sort(key=lambda item: item["path"])
        truncated = bool(stack) or len(entries) >= max_entries
        return {
            "path": resolved.display,
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
            "warnings": ["entry limit reached"] if truncated else [],
        }

    def list_files(self, args: dict[str, Any]) -> dict[str, Any]:
        base = self.workspace.existing(str(args.get("path", ".")))
        patterns = list(args.get("patterns") or [])
        if args.get("glob"):
            patterns.append(str(args["glob"]))
        excludes = list(args.get("exclude_patterns") or [])
        max_results = int(args.get("max_results", 5_000))
        files: list[dict[str, Any]] = []
        truncated = False
        for absolute, relative in self.workspace.iter_files(base, include_hidden=bool(args.get("include_hidden", False)), include_ignored=bool(args.get("include_ignored", False))):
            display = relative.as_posix()
            if patterns and not matches_any(display, patterns):
                continue
            if excludes and matches_any(display, excludes):
                continue
            try:
                size = absolute.stat().st_size
            except OSError:
                size = 0
            files.append({"path": display, "size_bytes": size, "modified": _iso_mtime(absolute)})
            if len(files) >= max_results:
                truncated = True
                break
        if args.get("sort", "path") == "modified":
            files.sort(key=lambda item: item["modified"], reverse=True)
        else:
            files.sort(key=lambda item: item["path"])
        return {"files": files, "count": len(files), "truncated": truncated}

    def search_text(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args["query"])
        flags = 0 if args.get("case_sensitive", False) else re.IGNORECASE
        try:
            expression = re.compile(query if args.get("regex", False) else re.escape(query), flags)
        except re.error as exc:
            raise ToolError("INVALID_REGEX", f"invalid regular expression: {exc}", "validation") from exc
        base = self.workspace.existing(str(args.get("path", ".")))
        include_patterns = list(args.get("include_globs") or [])
        if args.get("glob"):
            include_patterns.append(str(args["glob"]))
        exclude_patterns = list(args.get("exclude_globs") or [])
        context = int(args.get("context_lines", 0))
        max_results = int(args.get("max_results", 1_000))
        preview_bytes = int(args.get("max_preview_bytes", 512))
        matches: list[dict[str, Any]] = []
        truncated = False
        for absolute, relative in self.workspace.iter_files(base, include_hidden=False, include_ignored=False):
            display = relative.as_posix()
            if include_patterns and not matches_any(display, include_patterns):
                continue
            if exclude_patterns and matches_any(display, exclude_patterns):
                continue
            try:
                text = absolute.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lines = text.splitlines()
            for index, line in enumerate(lines):
                found = expression.search(line)
                if not found:
                    continue
                preview, _ = _truncate_text(line, preview_bytes)
                matches.append({
                    "path": display,
                    "line": index + 1,
                    "column": found.start() + 1,
                    "preview": preview,
                    "before": lines[max(0, index - context) : index],
                    "after": lines[index + 1 : index + 1 + context],
                })
                if len(matches) >= max_results:
                    truncated = True
                    break
            if truncated:
                break
        return {"query": query, "matches": matches, "total_matches": len(matches), "total_matches_exact": not truncated, "truncated": truncated}

    def apply_patch(self, args: dict[str, Any]) -> dict[str, Any]:
        return dict(apply_patch_envelope(self.workspace, str(args["patch"]), dry_run=bool(args.get("dry_run", False))))

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def _command_env(self, overrides: dict[str, str]) -> dict[str, str]:
        if self.permission_mode == "dangerous":
            env = os.environ.copy()
        else:
            allowed = {
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
            }
            env = {
                key: value
                for key, value in os.environ.items()
                if key.upper() in allowed and not SENSITIVE_ENV_RE.search(key)
            }
            env["PATH"] = (
                os.environ.get("PATH", "")
                if self._permission_granted("privileged_executable")
                else os.pathsep.join(self.safe_exec_path)
            )
            env.update({"HOME": str(self.commands.home_dir), "TMPDIR": str(self.commands.tmp_dir), "TEMP": str(self.commands.tmp_dir), "TMP": str(self.commands.tmp_dir)})
            if os.name == "nt":
                roaming = self.commands.home_dir / "AppData" / "Roaming"
                local = self.commands.home_dir / "AppData" / "Local"
                roaming.mkdir(parents=True, exist_ok=True)
                local.mkdir(parents=True, exist_ok=True)
                home_drive, home_tail = os.path.splitdrive(str(self.commands.home_dir))
                env.update(
                    {
                        "USERPROFILE": str(self.commands.home_dir),
                        "HOMEDRIVE": home_drive,
                        "HOMEPATH": home_tail or "\\",
                        "APPDATA": str(roaming),
                        "LOCALAPPDATA": str(local),
                    }
                )
        env.update(overrides)
        for internal_name in (
            BROKER_DIR_ENV,
            BROKER_SECRET_ENV,
            BROKER_SERVER_ID_ENV,
        ):
            env.pop(internal_name, None)
        if (
            self.permission_mode == "safe"
            and not self.allow_network
            and not self._permission_granted("network")
        ):
            # Best-effort offline defaults until an OS network sandbox backend
            # is applied to every child process.
            env.update(
                {
                    "GOPROXY": "off",
                    "GOTOOLCHAIN": "local",
                    "PIP_NO_INDEX": "1",
                    "npm_config_offline": "true",
                    "YARN_ENABLE_NETWORK": "0",
                    "CARGO_NET_OFFLINE": "true",
                }
            )
        return env

    def _validate_command(self, cmd: str, env: dict[str, str], timeout_ms: int) -> None:
        if self.permission_mode == "dangerous":
            return
        protected_names = {name.upper() for name in SANDBOX_PROTECTED_ENV}
        protected = [name for name in env if name.upper() in protected_names]
        if protected:
            raise ToolError(
                "PERMISSION_REQUIRED",
                "sandbox-controlled environment variables cannot be overridden outside dangerous mode",
                "permission",
                False,
                {"permission": "sandbox_env_override", "variables": protected},
            )
        sensitive = [name for name in env if SENSITIVE_ENV_RE.search(name)]
        if sensitive and not self._permission_granted("sensitive_env"):
            raise ToolError("PERMISSION_REQUIRED", "sensitive environment variables require dangerous mode", "permission", False, {"permission": "sensitive_env", "variables": sensitive})
        if (
            timeout_ms > 60_000
            and self.permission_mode == "safe"
            and not self._permission_granted("long_timeout")
        ):
            raise ToolError("PERMISSION_REQUIRED", "timeouts above 60 seconds require trusted mode", "permission", False, {"permission": "long_timeout"})
        checks = [
            (DESTRUCTIVE_RE, "destructive_command", "destructive command is blocked"),
            (
                GIT_METADATA_WRITE_RE,
                "git_metadata_write",
                "Git metadata-changing commands are blocked outside dangerous mode",
            ),
        ]
        if self.permission_mode == "safe":
            checks.extend([
                (SHELL_EXPANSION_RE, "shell_expansion", "shell expansion is blocked in safe mode"),
                (INLINE_SCRIPT_RE, "inline_script", "inline scripts are blocked in safe mode"),
            ])
            if not self.allow_network:
                checks.append((NETWORK_RE, "network", "network-looking commands are blocked in safe mode"))
                checks.append((NETWORK_COMMAND_RE, "network", "network-capable package or VCS command is blocked in safe mode"))
        for expression, permission, message in checks:
            if expression.search(cmd) and not self._permission_granted(permission):
                raise ToolError("PERMISSION_REQUIRED", message, "permission", False, {"permission": permission})
        redirect = REDIRECT_ESCAPE_RE.search(cmd)
        if redirect:
            raw_path = redirect.group(1)
            try:
                self.workspace.writable(raw_path)
            except ToolError as exc:
                raise ToolError("PERMISSION_REQUIRED", "shell redirection outside the workspace is blocked", "permission", False, {"permission": "write_generated_or_ignored", "path": raw_path}) from exc
        # Check obvious path arguments. System executable paths are allowed as
        # argv[0], but subsequent absolute/parent-relative paths must remain in
        # the workspace.
        try:
            tokens = shlex.split(cmd, posix=os.name != "nt")
        except ValueError as exc:
            raise ToolError("INVALID_COMMAND", f"cannot parse command: {exc}", "validation") from exc
        for index, token in enumerate(tokens):
            if index == 0 or token.startswith("-") or "://" in token:
                continue
            if token.startswith("~") and not self._permission_granted("shell_expansion"):
                raise ToolError(
                    "PERMISSION_REQUIRED",
                    "home-directory shell expansion is blocked outside dangerous mode",
                    "permission",
                    False,
                    {"permission": "shell_expansion", "path": token},
                )
            if token.startswith("/") or token.startswith("../") or token.startswith("..\\"):
                if token in {"/dev/null", "/dev/zero", "/dev/random", "/dev/urandom"}:
                    continue
                try:
                    self.workspace.writable(token)
                except ToolError as exc:
                    raise ToolError("PERMISSION_REQUIRED", "command path argument escapes the workspace", "permission", False, {"path": token}) from exc

    def _validate_process(
        self,
        program: str,
        argv: list[str],
        env: dict[str, str],
        timeout_ms: int,
    ) -> str:
        display = subprocess.list2cmdline([program, *argv])
        self._validate_command(display, env, timeout_ms)
        privileged = self.permission_mode == "dangerous" or self._permission_granted(
            "privileged_executable"
        )
        resolved = self.toolchains.resolve_program(program, privileged=privileged)
        if resolved is None:
            if not privileged:
                raise ToolError(
                    "PERMISSION_REQUIRED",
                    f"沙箱执行 PATH 中未找到 {program}；需要读取用户工具环境后重试。",
                    "permission",
                    False,
                    {
                        "permission": "privileged_executable",
                        "program": program,
                        "sandbox_path": list(self.safe_exec_path),
                    },
                )
            raise ToolError(
                "EXECUTABLE_NOT_FOUND",
                f"program is not available in either the sandbox or elevated user environment: {program}",
                "process",
                False,
                {
                    "program": program,
                    "safe_path": list(self.safe_exec_path),
                    "elevated_lookup": True,
                },
            )
        return resolved

    def exec_process(self, args: dict[str, Any]) -> dict[str, Any]:
        program = str(args["program"])
        argv = [str(item) for item in list(args.get("args") or [])]
        timeout_ms = int(args.get("timeout_ms", 30_000))
        env_overrides = {str(key): str(value) for key, value in dict(args.get("env") or {}).items()}
        resolved_program = self._validate_process(program, argv, env_overrides, timeout_ms)
        privileged_execution = (
            self.permission_mode == "dangerous"
            or self._permission_granted("privileged_executable")
        )
        cwd = self.workspace.existing(str(args.get("cwd") or args.get("workdir", "."))).absolute
        if not cwd.is_dir():
            raise ToolError("NOT_DIRECTORY", "process workdir is not a directory", "filesystem")

        command: list[str]
        if os.name == "nt" and Path(resolved_program).suffix.lower() in {".cmd", ".bat"}:
            unsafe = [item for item in argv if WINDOWS_BATCH_META_RE.search(item)]
            if unsafe:
                raise ToolError(
                    "PERMISSION_REQUIRED",
                    "Windows batch arguments containing cmd.exe metacharacters are blocked",
                    "permission",
                    False,
                    {
                        "permission": "shell_expansion",
                        "arguments": unsafe,
                    },
                )
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            quoted_program = f'"{resolved_program}"'
            quoted_args = " ".join(f'"{item}"' for item in argv)
            command_line = f'{quoted_program}{(" " + quoted_args) if quoted_args else ""}'
            command = [comspec, "/d", "/v:off", "/s", "/c", command_line]
        else:
            command = [resolved_program, *argv]
        command = self.process_sandbox.wrap(
            command,
            cwd=cwd,
            permissions=ACTIVE_PERMISSIONS.get(),
            readable_roots=(
                (
                    self.toolchains.readable_root_for_program(
                        program,
                        resolved_program,
                        privileged=True,
                    ),
                )
                if privileged_execution
                else ()
            ),
        )
        process_env = self._command_env(env_overrides)
        if privileged_execution:
            process_env["PATH"] = os.pathsep.join(
                [str(Path(resolved_program).resolve().parent), process_env.get("PATH", "")]
            )
        managed = self.commands.start(
            command,
            cwd=cwd,
            env=process_env,
            stdin_text=str(args.get("stdin", "")),
            timeout_ms=timeout_ms,
            tty=bool(args.get("tty", False)),
            shell=False,
        )
        self.commands.wait(managed, int(args.get("yield_time_ms", 10_000)))
        payload = command_payload(managed, int(args.get("max_output_bytes", 65_536)))
        payload["program"] = resolved_program
        payload["argv"] = argv
        payload["shell"] = False
        return self._format_command_payload(payload, args)

    @staticmethod
    def _shell_program_names(cmd: str) -> list[str]:
        """Extract external command heads from the restricted shell subset."""

        builtins = {
            ".", ":", "alias", "bg", "break", "cd", "continue", "echo",
            "eval", "exec", "exit", "export", "false", "fg", "getopts",
            "hash", "jobs", "printf", "pwd", "read", "readonly", "return",
            "set", "shift", "test", "times", "trap", "true", "type",
            "ulimit", "umask", "unalias", "unset", "wait",
        }
        prefixes = {"do", "done", "elif", "else", "fi", "if", "then", "while", "until"}
        control_heads = {"case", "esac", "for", "function", "in", "select", "{", "}"}
        wrappers = {"builtin", "command", "env", "time"}
        names: list[str] = []
        for segment in re.split(r"(?:&&|\|\||[;|])", cmd):
            try:
                tokens = shlex.split(segment, posix=os.name != "nt")
            except ValueError:
                continue
            if tokens and tokens[0] in control_heads:
                continue
            while tokens and (
                tokens[0] in prefixes
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0])
            ):
                tokens.pop(0)
            while tokens and tokens[0] in wrappers:
                tokens.pop(0)
                while tokens and (
                    tokens[0].startswith("-")
                    or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0])
                ):
                    tokens.pop(0)
            if not tokens:
                continue
            name = tokens[0]
            if name in builtins or "/" in name or "\\" in name:
                continue
            if name not in names:
                names.append(name)
        return names

    def exec_command(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = str(args["cmd"])
        timeout_ms = int(args.get("timeout_ms", 30_000))
        env_overrides = {str(key): str(value) for key, value in dict(args.get("env") or {}).items()}
        self._validate_command(cmd, env_overrides, timeout_ms)
        cwd = self.workspace.existing(str(args.get("cwd") or args.get("workdir", "."))).absolute
        if not cwd.is_dir():
            raise ToolError("NOT_DIRECTORY", "command workdir is not a directory", "filesystem")
        privileged = self.permission_mode == "dangerous" or self._permission_granted(
            "privileged_executable"
        )
        command_roots: list[Path] = []
        command_bins: list[Path] = []
        for program in self._shell_program_names(cmd):
            resolved = self.toolchains.resolve_program(program, privileged=privileged)
            if resolved is None:
                if not privileged:
                    raise ToolError(
                        "PERMISSION_REQUIRED",
                        f"沙箱执行 PATH 中未找到 {program}；需要读取用户工具环境后重试。",
                        "permission",
                        False,
                        {
                            "permission": "privileged_executable",
                            "program": program,
                            "sandbox_path": list(self.safe_exec_path),
                        },
                    )
                raise ToolError(
                    "EXECUTABLE_NOT_FOUND",
                    f"program is not available in either the sandbox or elevated user environment: {program}",
                    "process",
                    False,
                    {"program": program, "elevated_lookup": True},
                )
            if privileged:
                root = self.toolchains.readable_root_for_program(
                    program,
                    resolved,
                    privileged=True,
                )
                if root not in command_roots:
                    command_roots.append(root)
                bin_dir = Path(resolved).resolve().parent
                if bin_dir not in command_bins:
                    command_bins.append(bin_dir)
        if self.permission_mode == "dangerous":
            launch_command: str | list[str] = cmd
            launch_shell = True
        elif os.name == "nt":
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            launch_command = [comspec, "/d", "/s", "/c", cmd]
            launch_shell = False
        else:
            launch_command = ["/bin/sh", "-c", cmd]
            launch_shell = False
        if isinstance(launch_command, list):
            launch_command = self.process_sandbox.wrap(
                launch_command,
                cwd=cwd,
                permissions=ACTIVE_PERMISSIONS.get(),
                readable_roots=tuple(command_roots),
            )
        command_env = self._command_env(env_overrides)
        if privileged and command_bins:
            command_env["PATH"] = os.pathsep.join(
                [
                    *(str(path) for path in command_bins),
                    command_env.get("PATH", ""),
                ]
            )
        managed = self.commands.start(
            launch_command,
            cwd=cwd,
            env=command_env,
            stdin_text=str(args.get("stdin", "")),
            timeout_ms=timeout_ms,
            tty=bool(args.get("tty", False)),
            shell=launch_shell,
        )
        self.commands.wait(managed, int(args.get("yield_time_ms", 10_000)))
        return self._format_command_payload(
            command_payload(managed, int(args.get("max_output_bytes", 65_536))),
            args,
        )

    def write_stdin(self, args: dict[str, Any]) -> dict[str, Any]:
        managed = self.commands.write(str(args["command_id"]), str(args.get("chars", "")))
        self.commands.wait(managed, int(args.get("yield_time_ms", 10_000)))
        return self._format_command_payload(
            command_payload(managed, int(args.get("max_output_bytes", 65_536))),
            args,
        )

    def kill_command(self, args: dict[str, Any]) -> dict[str, Any]:
        command_id = str(args["command_id"])
        status = self.commands.terminate(command_id, str(args.get("signal", "TERM")), wait_ms=int(args.get("wait_ms", 5_000)), kill_wait_ms=int(args.get("kill_wait_ms", 2_000)))
        managed = self.commands.get(command_id)
        payload = command_payload(managed, int(args.get("max_output_bytes", 65_536)))
        payload["status"] = status
        return self._format_command_payload(payload, args)

    def _format_command_payload(
        self,
        payload: dict[str, Any],
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if payload.get("status") == "running" and payload.get("command_id"):
            payload["next_action"] = {
                "tool": "write_stdin",
                "arguments": {
                    "command_id": payload["command_id"],
                    "chars": "",
                    "yield_time_ms": 10_000,
                },
            }
        verbosity = str(args.get("verbosity") or "").strip().lower()
        if not verbosity or verbosity == "full":
            return payload
        if verbosity not in {"summary", "preview"}:
            raise ToolError(
                "INVALID_ARGUMENT",
                "verbosity must be one of: summary, preview, full",
                "validation",
            )
        elapsed = float(payload.get("elapsed_ms") or 0) / 1000.0
        exit_code = payload.get("exit_code")
        state = f"exit {exit_code}" if exit_code is not None else str(payload.get("status", "running"))
        summary = f"{state} | {elapsed:.1f}s"
        compact = {
            key: value
            for key, value in payload.items()
            if key not in {"stdout", "stderr"}
        }
        compact["summary"] = summary
        if verbosity == "preview":
            sections: list[str] = []
            stdout = payload.get("stdout")
            stderr = payload.get("stderr")
            if isinstance(stdout, str) and stdout:
                sections.append(f"--- stdout ---\n{stdout}")
            if isinstance(stderr, str) and stderr:
                sections.append(f"--- stderr ---\n{stderr}")
            preview, preview_truncated = _truncate_text(
                "\n".join(sections),
                int(args.get("preview_bytes", 4_096)),
            )
            compact["preview"] = preview
            compact["preview_truncated"] = preview_truncated
            compact["truncated"] = bool(compact.get("truncated") or preview_truncated)
        return compact

    def read_output(self, args: dict[str, Any]) -> dict[str, Any]:
        ref = str(args["output_ref"])
        match = re.fullmatch(r"command:([A-Za-z0-9_-]+):(stdout|stderr)", ref)
        if not match:
            raise ToolError("INVALID_OUTPUT_REF", "output_ref must be command:<id>:stdout|stderr", "validation")
        command = self.commands.get(match.group(1))
        ref_stream = match.group(2)
        stream = str(args.get("stream") or ref_stream)
        if stream != ref_stream:
            raise ToolError(
                "INVALID_ARGUMENT",
                "stream does not match output_ref",
                "validation",
            )
        return dict(self.commands.output(command, stream, int(args.get("offset", 0)), int(args.get("limit", 4_096))))

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

    # ------------------------------------------------------------------
    # Git
    # ------------------------------------------------------------------
    def _git(self, argv: list[str], *, cwd: Path | None = None, max_bytes: int = 262_144, check: bool = True) -> tuple[str, str, int, bool]:
        try:
            result = subprocess.run(["git", *argv], cwd=str(cwd or self.workspace.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, env=self._command_env({}))
        except FileNotFoundError as exc:
            raise ToolError("GIT_NOT_FOUND", "git executable is not available", "environment") from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolError("GIT_TIMEOUT", "git command timed out", "process", True) from exc
        stdout, out_cut = _truncate_text(result.stdout, max_bytes)
        stderr, err_cut = _truncate_text(result.stderr, max_bytes)
        if check and result.returncode != 0:
            raise ToolError("GIT_FAILED", stderr.strip() or "git command failed", "git", False, {"exit_code": result.returncode})
        return stdout, stderr, result.returncode, out_cut or err_cut

    def _git_repo(self, path: str = ".") -> tuple[Path, bool]:
        cwd = self.workspace.existing(path).absolute
        if cwd.is_file():
            cwd = cwd.parent
        _, _, code, _ = self._git(["rev-parse", "--is-inside-work-tree"], cwd=cwd, check=False, max_bytes=4_096)
        return cwd, code == 0

    def git_status(self, args: dict[str, Any]) -> dict[str, Any]:
        cwd, is_repo = self._git_repo(str(args.get("path", ".")))
        if not is_repo:
            return {
                "is_repo": False,
                "branch": "",
                "head": "",
                "upstream": "",
                "ahead": 0,
                "behind": 0,
                "entries": [],
                "clean": True,
                "truncated": False,
                "warnings": [],
            }
        untracked = "all" if args.get("include_untracked", True) else "no"
        output, _, _, truncated = self._git(
            ["status", "--porcelain=v1", "-b", f"--untracked-files={untracked}"],
            cwd=cwd,
            max_bytes=1_048_576,
        )
        max_entries = int(args.get("max_entries", 1_000))
        entries: list[dict[str, Any]] = []
        branch = ""
        upstream = ""
        ahead = 0
        behind = 0
        status_lines = 0
        for line in output.splitlines():
            if line.startswith("## "):
                branch, upstream, ahead, behind = _parse_git_branch_line(line[3:])
                continue
            if len(line) < 3:
                continue
            status_lines += 1
            if len(entries) >= max_entries:
                continue
            path_text = line[3:]
            original_path = None
            if " -> " in path_text:
                original_path, path_text = path_text.split(" -> ", 1)
            entries.append(
                {
                    "status": line[:2],
                    "path": path_text,
                    "original_path": original_path,
                    "index_status": line[0],
                    "worktree_status": line[1],
                }
            )
        head, _, _, _ = self._git(["rev-parse", "HEAD"], cwd=cwd, max_bytes=4_096, check=False)
        return {
            "is_repo": True,
            "branch": branch,
            "head": head.strip(),
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "entries": entries,
            "clean": status_lines == 0,
            "truncated": truncated or status_lines > len(entries),
            "warnings": ["entry limit reached"] if status_lines > len(entries) else [],
        }

    def _git_paths(self, args: dict[str, Any]) -> list[str]:
        values: list[str] = []
        if args.get("path"):
            values.append(str(args["path"]))
        values.extend(str(item) for item in args.get("paths") or [])
        result: list[str] = []
        for value in values:
            result.append(self.workspace.writable(value).display)
        return result

    def git_diff(self, args: dict[str, Any]) -> dict[str, Any]:
        context = int(args.get("context_lines", 3))
        max_bytes = int(args.get("max_bytes", 262_144))
        paths = self._git_paths(args)
        parts: list[str] = []
        truncated = False
        if args.get("unstaged", True):
            argv = ["diff", f"--unified={context}"]
            if paths:
                argv += ["--", *paths]
            text, _, _, cut = self._git(argv, max_bytes=max_bytes)
            parts.append(text)
            truncated |= cut
        if args.get("staged", False):
            argv = ["diff", "--cached", f"--unified={context}"]
            if paths:
                argv += ["--", *paths]
            text, _, _, cut = self._git(argv, max_bytes=max_bytes)
            parts.append(text)
            truncated |= cut
        diff, extra_cut = _truncate_text("".join(parts), max_bytes)
        is_truncated = truncated or extra_cut
        return {
            "diff": diff,
            "files": _parse_diff_files(diff),
            "truncated": is_truncated,
            "exit_code": 0,
            "warnings": ["diff truncated"] if is_truncated else [],
        }

    def git_log(self, args: dict[str, Any]) -> dict[str, Any]:
        cwd, is_repo = self._git_repo(str(args.get("path", ".")))
        if not is_repo:
            return {
                "is_repo": False,
                "commits": [],
                "count": 0,
                "truncated": False,
                "warnings": [],
            }
        ref = str(args.get("ref", "HEAD"))
        if not ref or ref.startswith("-") or any(char in ref for char in "\x00\r\n"):
            raise ToolError("INVALID_ARGUMENT", "invalid git revision", "validation")
        max_count = int(args.get("max_count", 20))
        skip = int(args.get("skip", 0))
        fmt = "%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s%x1e"
        output, _, _, output_truncated = self._git(
            [
                "log",
                ref,
                f"--max-count={max_count + 1}",
                f"--skip={skip}",
                f"--format={fmt}",
            ],
            cwd=cwd,
        )
        commits = []
        for record in output.strip("\x1e\n").split("\x1e"):
            if not record.strip():
                continue
            fields = record.strip().split("\x1f", 5)
            if len(fields) == 6:
                commits.append(
                    {
                        "hash": fields[0],
                        "short_hash": fields[1],
                        "author_name": fields[2],
                        "author_email": fields[3],
                        "author_date": fields[4],
                        "date": fields[4],
                        "subject": fields[5],
                    }
                )
        has_more = len(commits) > max_count
        commits = commits[:max_count]
        result: dict[str, Any] = {
            "is_repo": True,
            "ref": ref,
            "path": str(args.get("path", ".")),
            "max_count": max_count,
            "skip": skip,
            "commits": commits,
            "count": len(commits),
            "truncated": output_truncated or has_more,
            "warnings": ["commit limit reached"] if has_more else [],
        }
        if has_more:
            result["next_action"] = {
                "tool": "git_log",
                "arguments": {
                    "path": str(args.get("path", ".")),
                    "ref": ref,
                    "max_count": max_count,
                    "skip": skip + max_count,
                },
            }
        return result

    def git_show(self, args: dict[str, Any]) -> dict[str, Any]:
        _, is_repo = self._git_repo(".")
        if not is_repo:
            return {
                "is_repo": False,
                "content": "",
                "output": "",
                "files": [],
                "truncated": False,
                "warnings": [],
            }
        max_bytes = int(args.get("max_bytes", 262_144))
        rev = str(args.get("rev", "HEAD"))
        if not rev or rev.startswith("-") or any(char in rev for char in "\x00\r\n"):
            raise ToolError("INVALID_ARGUMENT", "invalid git revision", "validation")
        argv = ["show", rev, f"--unified={int(args.get('context_lines', 3))}"]
        if not args.get("include_diff", True):
            argv.append("--no-patch")
        paths = self._git_paths(args)
        if paths:
            argv += ["--", *paths]
        output, stderr, code, truncated = self._git(argv, max_bytes=max_bytes, check=False)
        if code != 0:
            raise ToolError("GIT_FAILED", stderr.strip() or "git show failed", "git", False, {"exit_code": code})
        return {
            "is_repo": True,
            "content": output,
            "output": output,
            "rev": rev,
            "files": _parse_diff_files(output),
            "truncated": truncated,
            "exit_code": code,
            "warnings": ["output truncated"] if truncated else [],
        }

    def git_blame(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.workspace.existing(str(args["path"]))
        if not resolved.absolute.is_file():
            raise ToolError("NOT_FILE", "git_blame path must be a file", "validation")
        _, is_repo = self._git_repo(".")
        if not is_repo:
            return {
                "is_repo": False,
                "path": resolved.display,
                "lines": [],
                "entries": [],
                "truncated": False,
                "warnings": [],
            }
        start = int(args.get("start_line", 1))
        end = args.get("end_line")
        max_lines = int(args.get("max_lines", 200))
        requested_end = int(end) if end is not None else start + max_lines - 1
        if requested_end < start:
            raise ToolError("INVALID_ARGUMENT", "end_line must be >= start_line", "validation")
        final_line = min(requested_end, start + max_lines - 1)
        argv = ["blame", "--line-porcelain", "-L", f"{start},{final_line}"]
        if args.get("rev"):
            argv.append(str(args["rev"]))
        argv += ["--", resolved.display]
        output, stderr, code, truncated = self._git(argv, max_bytes=1_048_576, check=False)
        if code != 0:
            raise ToolError("GIT_FAILED", stderr.strip() or "git blame failed", "git", False, {"exit_code": code})
        entries: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line in output.splitlines():
            header = re.match(r"^([0-9a-f^]{40})\s+(\d+)\s+(\d+)(?:\s+(\d+))?$", line)
            if header:
                current = {"commit": header.group(1), "original_line": int(header.group(2)), "line": int(header.group(3))}
                entries.append(current)
            elif current is not None and line.startswith("author "):
                current["author"] = line[7:]
            elif current is not None and line.startswith("author-mail "):
                mail = line[12:].strip("<>")
                current["author_mail"] = mail
                current["author_email"] = mail
            elif current is not None and line.startswith("summary "):
                current["summary"] = line[8:]
            elif current is not None and line.startswith("\t"):
                current["content"] = line[1:]
        if len(entries) > max_lines:
            entries = entries[:max_lines]
            truncated = True
        truncated = truncated or requested_end > final_line
        result: dict[str, Any] = {
            "is_repo": True,
            "path": resolved.display,
            "rev": str(args["rev"]) if args.get("rev") else None,
            "start_line": start,
            "end_line": final_line,
            "max_lines": max_lines,
            "lines": entries,
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
            "warnings": ["line limit reached"] if truncated else [],
        }
        if requested_end > final_line:
            next_args: dict[str, Any] = {
                "path": str(args["path"]),
                "start_line": final_line + 1,
                "end_line": requested_end,
                "max_lines": max_lines,
            }
            if args.get("rev"):
                next_args["rev"] = str(args["rev"])
            result["next_action"] = {
                "tool": "git_blame",
                "arguments": next_args,
            }
        return result

    # ------------------------------------------------------------------
    # Image
    # ------------------------------------------------------------------
    def view_image(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.workspace.existing(str(args["path"]))
        if not resolved.absolute.is_file():
            raise ToolError("NOT_FILE", "image path must be a file", "validation")
        mime_type = mimetypes.guess_type(resolved.absolute.name)[0] or "application/octet-stream"
        if not mime_type.startswith("image/"):
            raise ToolError("NOT_IMAGE", f"unsupported image type: {mime_type}", "validation")
        try:
            data = resolved.absolute.read_bytes()
        except OSError as exc:
            raise ToolError("READ_FAILED", "cannot read image", "filesystem", True, {"error": str(exc)}) from exc
        max_bytes = int(args.get("max_bytes", 5_242_880))
        width = height = None
        resized = False
        warnings: list[str] = []
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                need_resize = bool(args.get("auto_resize", True)) and (len(data) > max_bytes or width > int(args.get("max_width", 2_000)) or height > int(args.get("max_height", 2_000)))
                if need_resize:
                    converted = image.convert("RGBA" if image.mode in {"RGBA", "LA"} else "RGB")
                    converted.thumbnail((int(args.get("max_width", 2_000)), int(args.get("max_height", 2_000))))
                    output = io.BytesIO()
                    fmt = "PNG" if mime_type == "image/png" else "WEBP" if mime_type == "image/webp" else "JPEG"
                    if fmt == "JPEG" and converted.mode != "RGB":
                        converted = converted.convert("RGB")
                    converted.save(output, format=fmt, quality=85, optimize=True)
                    data = output.getvalue()
                    width, height = converted.size
                    mime_type = {"PNG": "image/png", "WEBP": "image/webp", "JPEG": "image/jpeg"}[fmt]
                    resized = True
        except ImportError:
            warnings.append("Pillow is not installed; dimensions/auto-resize are unavailable")
        except Exception as exc:
            warnings.append(f"image metadata/resize failed: {exc}")
        if len(data) > max_bytes:
            raise ToolError("IMAGE_TOO_LARGE", "image exceeds max_bytes after resize attempt", "validation", False, {"bytes": len(data), "max_bytes": max_bytes, "warnings": warnings})
        return {
            "path": resolved.display,
            "mime_type": mime_type,
            "size_bytes": len(data),
            "width": width,
            "height": height,
            "resized": resized,
            "warnings": warnings,
            "_image": (mime_type, base64.b64encode(data).decode("ascii")),
        }

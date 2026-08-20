from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable

from ...errors import ToolError


SENSITIVE_ENV_RE = re.compile(
    r"(token|secret|credential|api[_-]?key|password|passwd|private)", re.I
)

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

NETWORK_RE = re.compile(
    r"(https?://|\bcurl\b|\bwget\b|\bssh\b|\bscp\b|\bftp\b|\bnc\b|\bnetcat\b|socket\.|requests\.|urllib\.|httpx\b|aiohttp\b)",
    re.I,
)
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
INLINE_SCRIPT_RE = re.compile(
    r"\b(python(?:3)?\s+-c|node\s+-e|ruby\s+-e|perl\s+-e|(?:ba|z|)sh\s+-c)\b",
    re.I,
)
DESTRUCTIVE_RE = re.compile(
    r"(^|\s)(sudo\b|su\b|mkfs\b|mount\b|umount\b|chmod\s+-R\b|chown\s+-R\b|rm\s+-[^\s]*r[^\s]*f\b|rm\s+-[^\s]*f[^\s]*r\b)",
    re.I,
)
REDIRECT_ESCAPE_RE = re.compile(r"(?:^|\s)(?:>|>>|<)\s*(/[^\s]+|\.\./[^\s]+)")


class ProcessCommandPolicy:
    """Validate command-level permissions without owning process execution."""

    def __init__(
        self,
        *,
        permission_mode: str,
        allow_network: bool,
        permission_granted: Callable[[str], bool],
        validate_writable_path: Callable[[str], object],
    ) -> None:
        self.permission_mode = permission_mode
        self.allow_network = allow_network
        self.permission_granted = permission_granted
        self.validate_writable_path = validate_writable_path

    def validate(
        self,
        cmd: str,
        env: dict[str, str],
        timeout_ms: int,
    ) -> None:
        if self.permission_mode == "dangerous":
            return
        self._validate_environment(env)
        self._validate_timeout(timeout_ms)
        self._validate_patterns(cmd)
        self._validate_redirect(cmd)
        self._validate_path_arguments(cmd)

    def _validate_environment(self, env: dict[str, str]) -> None:
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
        if sensitive and not self.permission_granted("sensitive_env"):
            raise ToolError(
                "PERMISSION_REQUIRED",
                "sensitive environment variables require dangerous mode",
                "permission",
                False,
                {"permission": "sensitive_env", "variables": sensitive},
            )

    def _validate_timeout(self, timeout_ms: int) -> None:
        if (
            timeout_ms > 60_000
            and self.permission_mode == "safe"
            and not self.permission_granted("long_timeout")
        ):
            raise ToolError(
                "PERMISSION_REQUIRED",
                "timeouts above 60 seconds require trusted mode",
                "permission",
                False,
                {"permission": "long_timeout"},
            )

    def _validate_patterns(self, cmd: str) -> None:
        checks = [
            (DESTRUCTIVE_RE, "destructive_command", "destructive command is blocked"),
            (
                GIT_METADATA_WRITE_RE,
                "git_metadata_write",
                "Git metadata-changing commands are blocked outside dangerous mode",
            ),
        ]
        if self.permission_mode == "safe":
            checks.extend(
                [
                    (
                        SHELL_EXPANSION_RE,
                        "shell_expansion",
                        "shell expansion is blocked in safe mode",
                    ),
                    (
                        INLINE_SCRIPT_RE,
                        "inline_script",
                        "inline scripts are blocked in safe mode",
                    ),
                ]
            )
            if not self.allow_network:
                checks.extend(
                    [
                        (
                            NETWORK_RE,
                            "network",
                            "network-looking commands are blocked in safe mode",
                        ),
                        (
                            NETWORK_COMMAND_RE,
                            "network",
                            "network-capable package or VCS command is blocked in safe mode",
                        ),
                    ]
                )
        for expression, permission, message in checks:
            if expression.search(cmd) and not self.permission_granted(permission):
                raise ToolError(
                    "PERMISSION_REQUIRED",
                    message,
                    "permission",
                    False,
                    {"permission": permission},
                )

    def _validate_redirect(self, cmd: str) -> None:
        redirect = REDIRECT_ESCAPE_RE.search(cmd)
        if redirect is None:
            return
        raw_path = redirect.group(1)
        try:
            self.validate_writable_path(raw_path)
        except ToolError as exc:
            raise ToolError(
                "PERMISSION_REQUIRED",
                "shell redirection outside the workspace is blocked",
                "permission",
                False,
                {
                    "permission": "write_generated_or_ignored",
                    "path": raw_path,
                },
            ) from exc

    def _validate_path_arguments(self, cmd: str) -> None:
        try:
            tokens = shlex.split(cmd, posix=os.name != "nt")
        except ValueError as exc:
            raise ToolError(
                "INVALID_COMMAND", f"cannot parse command: {exc}", "validation"
            ) from exc
        for index, token in enumerate(tokens):
            if index == 0 or token.startswith("-") or "://" in token:
                continue
            if token.startswith("~") and not self.permission_granted("shell_expansion"):
                raise ToolError(
                    "PERMISSION_REQUIRED",
                    "home-directory shell expansion is blocked outside dangerous mode",
                    "permission",
                    False,
                    {"permission": "shell_expansion", "path": token},
                )
            if not (
                token.startswith("/")
                or token.startswith("../")
                or token.startswith("..\\")
            ):
                continue
            if token in {
                "/dev/null",
                "/dev/zero",
                "/dev/random",
                "/dev/urandom",
            }:
                continue
            try:
                self.validate_writable_path(token)
            except ToolError as exc:
                raise ToolError(
                    "PERMISSION_REQUIRED",
                    "command path argument escapes the workspace",
                    "permission",
                    False,
                    {"path": token},
                ) from exc


__all__ = [
    "DESTRUCTIVE_RE",
    "GIT_METADATA_WRITE_RE",
    "INLINE_SCRIPT_RE",
    "NETWORK_COMMAND_RE",
    "NETWORK_RE",
    "ProcessCommandPolicy",
    "REDIRECT_ESCAPE_RE",
    "SANDBOX_PROTECTED_ENV",
    "SENSITIVE_ENV_RE",
    "SHELL_EXPANSION_RE",
    "WINDOWS_BATCH_META_RE",
]

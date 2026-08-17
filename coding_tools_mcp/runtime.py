"""Business runtime for the 18 Coding Tools MCP tools."""

from __future__ import annotations

import base64
import fnmatch
import io
import logging
import mimetypes
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .errors import RpcError, ToolError
from .patching import apply_patch as apply_patch_envelope
from .processes import CommandManager, command_payload
from .project_context import ProjectContext, load_project_context
from .protocol import KNOWN_PROTOCOL_VERSIONS, RequestContext
from .results import make_tool_result
from .schemas import TOOL_SPECS, exposed_specs, validate_value
from .workspace import Workspace, matches_any


LOGGER = logging.getLogger(__name__)


SERVER_NAME = "coding-tools-mcp"
SERVER_TITLE = "Coding Tools MCP"
ENDPOINT_PATH = "/mcp"
PERMISSION_MODES = ("safe", "trusted", "dangerous")

SENSITIVE_ENV_RE = re.compile(r"(token|secret|credential|api[_-]?key|password|passwd|private)", re.I)
NETWORK_RE = re.compile(r"(https?://|\bcurl\b|\bwget\b|\bssh\b|\bscp\b|\bftp\b|\bnc\b|\bnetcat\b|socket\.|requests\.|urllib\.|httpx\b|aiohttp\b)", re.I)
SHELL_EXPANSION_RE = re.compile(r"(`|\$\(|\$\{)")
INLINE_SCRIPT_RE = re.compile(r"\b(python(?:3)?\s+-c|node\s+-e|ruby\s+-e|perl\s+-e|(?:ba|z|)sh\s+-c)\b", re.I)
DESTRUCTIVE_RE = re.compile(r"(^|\s)(sudo\b|su\b|mkfs\b|mount\b|umount\b|chmod\s+-R\b|chown\s+-R\b|rm\s+-[^\s]*r[^\s]*f\b|rm\s+-[^\s]*f[^\s]*r\b|git\b[^;&|]*\breset\s+--hard\b|git\b[^;&|]*\bclean\s+-[^\s]*[fx])", re.I)
REDIRECT_ESCAPE_RE = re.compile(r"(?:^|\s)(?:>|>>|<)\s*(/[^\s]+|\.\./[^\s]+)")


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
        self._specs = exposed_specs(enable_view_image=enable_view_image)
        self._spec_map = {spec.name: spec for spec in self._specs}

    def close(self) -> None:
        self.commands.close()

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
        try:
            payload = handler(arguments)
            payload.setdefault("ok", True)
            image_value = payload.pop("_image", None)
            if isinstance(image_value, tuple) and len(image_value) == 2:
                image = (str(image_value[0]), str(image_value[1]))
        except ToolError as exc:
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
            "workspace": str(self.workspace.root),
            "permission_mode": self.permission_mode,
            "auth_enabled": self.auth_enabled(),
            "supported_protocol_versions": list(KNOWN_PROTOCOL_VERSIONS),
            "endpoint_path": ENDPOINT_PATH,
            "runtime_dir": str(self.commands.runtime_dir),
            "network_allowed": self.allow_network,
            "exec_policy": {
                "shell_expansion": "allowed" if self.permission_mode != "safe" else "blocked",
                "inline_script": "allowed" if self.permission_mode != "safe" else "blocked",
                "secret_env_filter": self.permission_mode != "dangerous",
                "global_tmp_write": "allowed" if self.permission_mode == "dangerous" else "blocked",
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
        return {
            "workspace": str(self.workspace.root),
            "permission_mode": self.permission_mode,
            "network_allowed": self.allow_network,
            "runtime_dir": str(self.commands.runtime_dir),
            "home": str(self.commands.home_dir),
            "tmpdir": str(self.commands.tmp_dir),
            "cache_dir": str(self.commands.cache_dir),
            "sandbox": {
                "type": "application-policy",
                "os_kernel_sandbox": False,
                "note": "Filesystem tools are workspace-confined; exec_command is guarded by command policy but is not a kernel sandbox.",
            },
        }

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------
    def read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.workspace.existing(str(args["path"]))
        if not resolved.absolute.is_file():
            raise ToolError("NOT_FILE", f"not a file: {resolved.display}", "filesystem")
        try:
            text = resolved.absolute.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("NOT_UTF8", f"file is not valid UTF-8: {resolved.display}", "validation") from exc
        except OSError as exc:
            raise ToolError("READ_FAILED", f"cannot read file: {resolved.display}", "filesystem", True, {"error": str(exc)}) from exc
        lines = text.splitlines()
        start = int(args.get("start_line", 1))
        requested_end = args.get("end_line")
        max_lines = int(args.get("max_lines") or len(lines) or 1)
        end = int(requested_end) if requested_end is not None else min(len(lines), start + max_lines - 1)
        if end < start:
            raise ToolError("INVALID_RANGE", "end_line must be greater than or equal to start_line", "validation")
        selected = lines[start - 1 : end]
        content = "\n".join(selected)
        if selected and text.endswith("\n") and end >= len(lines):
            content += "\n"
        content, bytes_truncated = _truncate_text(content, int(args.get("max_bytes", 131_072)))
        actual_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        actual_end = min(end, start + max(0, actual_lines - 1)) if content else start - 1
        truncated = bytes_truncated or end < len(lines)
        return {
            "path": resolved.display,
            "content": content,
            "encoding": "utf-8",
            "start_line": start,
            "end_line": actual_end,
            "total_lines": len(lines),
            "truncated": truncated,
            "next_start_line": actual_end + 1 if truncated and actual_end < len(lines) else None,
        }

    def list_dir(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.workspace.existing(str(args.get("path", ".")))
        if not resolved.absolute.is_dir():
            raise ToolError("NOT_DIRECTORY", f"not a directory: {resolved.display}", "filesystem")
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
            entries.sort(key=lambda item: item["modified"], reverse=True)
        else:
            entries.sort(key=lambda item: item["path"])
        return {"path": resolved.display, "entries": entries, "count": len(entries), "truncated": bool(stack) or len(entries) >= max_entries}

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
            allowed = {"PATH", "LANG", "LC_ALL", "TERM", "PATHEXT", "COMSPEC", "SYSTEMROOT", "WINDIR"}
            env = {key: value for key, value in os.environ.items() if key in allowed and not SENSITIVE_ENV_RE.search(key)}
            env.update({"HOME": str(self.commands.home_dir), "TMPDIR": str(self.commands.tmp_dir), "TEMP": str(self.commands.tmp_dir), "TMP": str(self.commands.tmp_dir)})
        env.update(overrides)
        return env

    def _validate_command(self, cmd: str, env: dict[str, str], timeout_ms: int) -> None:
        if self.permission_mode == "dangerous":
            return
        sensitive = [name for name in env if SENSITIVE_ENV_RE.search(name)]
        if sensitive:
            raise ToolError("PERMISSION_REQUIRED", "sensitive environment variables require dangerous mode", "permission", False, {"permission": "sensitive_env", "variables": sensitive})
        if timeout_ms > 60_000 and self.permission_mode == "safe":
            raise ToolError("PERMISSION_REQUIRED", "timeouts above 60 seconds require trusted mode", "permission", False, {"permission": "long_timeout"})
        checks = [
            (DESTRUCTIVE_RE, "destructive_command", "destructive command is blocked"),
        ]
        if self.permission_mode == "safe":
            checks.extend([
                (SHELL_EXPANSION_RE, "shell_expansion", "shell expansion is blocked in safe mode"),
                (INLINE_SCRIPT_RE, "inline_script", "inline scripts are blocked in safe mode"),
            ])
            if not self.allow_network:
                checks.append((NETWORK_RE, "network", "network-looking commands are blocked in safe mode"))
        for expression, permission, message in checks:
            if expression.search(cmd):
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
            if token.startswith("/") or token.startswith("../") or token.startswith("..\\"):
                if token in {"/dev/null", "/dev/zero", "/dev/random", "/dev/urandom"}:
                    continue
                try:
                    self.workspace.writable(token)
                except ToolError as exc:
                    raise ToolError("PERMISSION_REQUIRED", "command path argument escapes the workspace", "permission", False, {"path": token}) from exc

    def exec_command(self, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("tty"):
            raise ToolError("TTY_UNSUPPORTED", "TTY mode is not implemented by this desktop server", "validation")
        cmd = str(args["cmd"])
        timeout_ms = int(args.get("timeout_ms", 30_000))
        env_overrides = {str(key): str(value) for key, value in dict(args.get("env") or {}).items()}
        self._validate_command(cmd, env_overrides, timeout_ms)
        cwd = self.workspace.existing(str(args.get("cwd") or args.get("workdir", "."))).absolute
        if not cwd.is_dir():
            raise ToolError("NOT_DIRECTORY", "command workdir is not a directory", "filesystem")
        managed = self.commands.start(cmd, cwd=cwd, env=self._command_env(env_overrides), stdin_text=str(args.get("stdin", "")), timeout_ms=timeout_ms)
        self.commands.wait(managed, int(args.get("yield_time_ms", 10_000)))
        return command_payload(managed, int(args.get("max_output_bytes", 65_536)))

    def write_stdin(self, args: dict[str, Any]) -> dict[str, Any]:
        managed = self.commands.write(str(args["command_id"]), str(args.get("chars", "")))
        self.commands.wait(managed, int(args.get("yield_time_ms", 10_000)))
        return command_payload(managed, int(args.get("max_output_bytes", 65_536)))

    def kill_command(self, args: dict[str, Any]) -> dict[str, Any]:
        command_id = str(args["command_id"])
        status = self.commands.terminate(command_id, str(args.get("signal", "TERM")), wait_ms=int(args.get("wait_ms", 5_000)), kill_wait_ms=int(args.get("kill_wait_ms", 2_000)))
        managed = self.commands.get(command_id)
        payload = command_payload(managed, int(args.get("max_output_bytes", 65_536)))
        payload["status"] = status
        return payload

    def read_output(self, args: dict[str, Any]) -> dict[str, Any]:
        ref = str(args["output_ref"])
        match = re.fullmatch(r"command:([A-Za-z0-9_-]+):(stdout|stderr)", ref)
        if not match:
            raise ToolError("INVALID_OUTPUT_REF", "output_ref must be command:<id>:stdout|stderr", "validation")
        command = self.commands.get(match.group(1))
        stream = str(args.get("stream") or match.group(2))
        return dict(self.commands.output(command, stream, int(args.get("offset", 0)), int(args.get("limit", 4_096))))

    def request_permissions(self, args: dict[str, Any]) -> dict[str, Any]:
        permission = str(args["permission"])
        if self.permission_mode == "dangerous":
            allowed = True
        elif self.permission_mode == "trusted":
            allowed = permission in {"network", "shell_expansion", "inline_script", "long_timeout"}
        else:
            allowed = permission == "write_generated_or_ignored" and args.get("tool_name") == "apply_patch"
        return {
            "tool_name": args["tool_name"],
            "permission": permission,
            "status": "granted" if allowed else "denied",
            "granted": allowed,
            "permission_mode": self.permission_mode,
            "reason": args["reason"],
            "note": "This tool reports policy only; it never changes server permissions.",
        }

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
            return {"is_repo": False, "entries": [], "clean": True}
        untracked = "all" if args.get("include_untracked", True) else "no"
        output, _, _, truncated = self._git(["status", "--porcelain=v1", f"--untracked-files={untracked}"], cwd=cwd)
        entries = []
        for line in output.splitlines()[: int(args.get("max_entries", 1_000))]:
            if len(line) < 3:
                continue
            entries.append({"status": line[:2], "path": line[3:]})
        branch, _, _, _ = self._git(["branch", "--show-current"], cwd=cwd, max_bytes=4_096, check=False)
        head, _, _, _ = self._git(["rev-parse", "HEAD"], cwd=cwd, max_bytes=4_096, check=False)
        return {"is_repo": True, "branch": branch.strip(), "head": head.strip(), "entries": entries, "clean": not entries, "truncated": truncated or len(output.splitlines()) > len(entries)}

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
        return {"diff": diff, "truncated": truncated or extra_cut, "exit_code": 0}

    def git_log(self, args: dict[str, Any]) -> dict[str, Any]:
        cwd, is_repo = self._git_repo(str(args.get("path", ".")))
        if not is_repo:
            return {"is_repo": False, "commits": []}
        fmt = "%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s%x1e"
        output, _, _, truncated = self._git(["log", str(args.get("ref", "HEAD")), f"--max-count={int(args.get('max_count', 20))}", f"--skip={int(args.get('skip', 0))}", f"--format={fmt}"], cwd=cwd)
        commits = []
        for record in output.strip("\x1e\n").split("\x1e"):
            if not record.strip():
                continue
            fields = record.strip().split("\x1f", 5)
            if len(fields) == 6:
                commits.append({"hash": fields[0], "short_hash": fields[1], "author_name": fields[2], "author_email": fields[3], "date": fields[4], "subject": fields[5]})
        return {"is_repo": True, "commits": commits, "count": len(commits), "truncated": truncated}

    def git_show(self, args: dict[str, Any]) -> dict[str, Any]:
        max_bytes = int(args.get("max_bytes", 262_144))
        argv = ["show", str(args.get("rev", "HEAD")), f"--unified={int(args.get('context_lines', 3))}"]
        if not args.get("include_diff", True):
            argv.append("--no-patch")
        paths = self._git_paths(args)
        if paths:
            argv += ["--", *paths]
        output, stderr, code, truncated = self._git(argv, max_bytes=max_bytes, check=False)
        if code != 0:
            raise ToolError("GIT_FAILED", stderr.strip() or "git show failed", "git", False, {"exit_code": code})
        return {"output": output, "rev": str(args.get("rev", "HEAD")), "truncated": truncated, "exit_code": code}

    def git_blame(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.workspace.existing(str(args["path"]))
        if not resolved.absolute.is_file():
            raise ToolError("NOT_FILE", "git_blame path must be a file", "validation")
        start = int(args.get("start_line", 1))
        end = args.get("end_line")
        if end is None:
            end = start + int(args.get("max_lines", 200)) - 1
        argv = ["blame", "--line-porcelain", "-L", f"{start},{int(end)}"]
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
                current["author_email"] = line[12:].strip("<>")
            elif current is not None and line.startswith("summary "):
                current["summary"] = line[8:]
            elif current is not None and line.startswith("\t"):
                current["content"] = line[1:]
        max_lines = int(args.get("max_lines", 200))
        if len(entries) > max_lines:
            entries = entries[:max_lines]
            truncated = True
        return {"path": resolved.display, "entries": entries, "count": len(entries), "truncated": truncated}

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
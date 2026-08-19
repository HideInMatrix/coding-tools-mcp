from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from ...errors import ToolError
from ...local_permission_broker import (
    BROKER_DIR_ENV,
    BROKER_SECRET_ENV,
    BROKER_SERVER_ID_ENV,
)
from ...permissions import ACTIVE_PERMISSIONS
from ...processes import command_payload
from ...route_probe import ROUTE_PROBE_TOKEN_ENV
from .._shared import truncate_text
from .policy import (
    DESTRUCTIVE_RE,
    GIT_METADATA_WRITE_RE,
    INLINE_SCRIPT_RE,
    NETWORK_COMMAND_RE,
    NETWORK_RE,
    REDIRECT_ESCAPE_RE,
    SANDBOX_PROTECTED_ENV,
    SENSITIVE_ENV_RE,
    SHELL_EXPANSION_RE,
    WINDOWS_BATCH_META_RE,
)


class ProcessHandlers:
    """Command execution, process lifecycle and retained output handlers."""

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
            env.update(
                {
                    "HOME": str(self.commands.home_dir),
                    "TMPDIR": str(self.commands.tmp_dir),
                    "TEMP": str(self.commands.tmp_dir),
                    "TMP": str(self.commands.tmp_dir),
                }
            )
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
            ROUTE_PROBE_TOKEN_ENV,
        ):
            env.pop(internal_name, None)
        if (
            self.permission_mode == "safe"
            and not self.allow_network
            and not self._permission_granted("network")
        ):
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

    def _validate_command(
        self,
        cmd: str,
        env: dict[str, str],
        timeout_ms: int,
    ) -> None:
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
            raise ToolError(
                "PERMISSION_REQUIRED",
                "sensitive environment variables require dangerous mode",
                "permission",
                False,
                {"permission": "sensitive_env", "variables": sensitive},
            )
        if (
            timeout_ms > 60_000
            and self.permission_mode == "safe"
            and not self._permission_granted("long_timeout")
        ):
            raise ToolError(
                "PERMISSION_REQUIRED",
                "timeouts above 60 seconds require trusted mode",
                "permission",
                False,
                {"permission": "long_timeout"},
            )
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
                checks.append(
                    (
                        NETWORK_RE,
                        "network",
                        "network-looking commands are blocked in safe mode",
                    )
                )
                checks.append(
                    (
                        NETWORK_COMMAND_RE,
                        "network",
                        "network-capable package or VCS command is blocked in safe mode",
                    )
                )
        for expression, permission, message in checks:
            if expression.search(cmd) and not self._permission_granted(permission):
                raise ToolError(
                    "PERMISSION_REQUIRED",
                    message,
                    "permission",
                    False,
                    {"permission": permission},
                )
        redirect = REDIRECT_ESCAPE_RE.search(cmd)
        if redirect:
            raw_path = redirect.group(1)
            try:
                self.workspace.writable(raw_path)
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
        try:
            tokens = shlex.split(cmd, posix=os.name != "nt")
        except ValueError as exc:
            raise ToolError(
                "INVALID_COMMAND", f"cannot parse command: {exc}", "validation"
            ) from exc
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
                if token in {
                    "/dev/null",
                    "/dev/zero",
                    "/dev/random",
                    "/dev/urandom",
                }:
                    continue
                try:
                    self.workspace.writable(token)
                except ToolError as exc:
                    raise ToolError(
                        "PERMISSION_REQUIRED",
                        "command path argument escapes the workspace",
                        "permission",
                        False,
                        {"path": token},
                    ) from exc

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
        program_key = os.path.normcase(program.strip())
        if not privileged and program_key in self._privileged_program_misses:
            raise ToolError(
                "EXECUTABLE_NOT_FOUND",
                f"宿主机用户环境已查询过，仍未找到 {program}；本次 MCP Server 会话不再重复请求环境权限。",
                "process",
                False,
                {
                    "program": program,
                    "safe_path": list(self.safe_exec_path),
                    "elevated_lookup": True,
                    "cached_miss": True,
                    "hint": "安装或调整工具环境后，请重启当前 MCP Server 再重新检测。",
                },
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
            self._privileged_program_misses.add(program_key)
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
        if privileged:
            self._privileged_program_misses.discard(program_key)
        return resolved

    def exec_process(self, args: dict[str, Any]) -> dict[str, Any]:
        program = str(args["program"])
        argv = [str(item) for item in list(args.get("args") or [])]
        timeout_ms = int(args.get("timeout_ms", 30_000))
        env_overrides = {
            str(key): str(value) for key, value in dict(args.get("env") or {}).items()
        }
        resolved_program = self._validate_process(
            program, argv, env_overrides, timeout_ms
        )
        privileged_execution = (
            self.permission_mode == "dangerous"
            or self._permission_granted("privileged_executable")
        )
        cwd = self.workspace.existing(
            str(args.get("cwd") or args.get("workdir", "."))
        ).absolute
        if not cwd.is_dir():
            raise ToolError(
                "NOT_DIRECTORY", "process workdir is not a directory", "filesystem"
            )

        command: list[str]
        if os.name == "nt" and Path(resolved_program).suffix.lower() in {".cmd", ".bat"}:
            unsafe = [item for item in argv if WINDOWS_BATCH_META_RE.search(item)]
            if unsafe:
                raise ToolError(
                    "PERMISSION_REQUIRED",
                    "Windows batch arguments containing cmd.exe metacharacters are blocked",
                    "permission",
                    False,
                    {"permission": "shell_expansion", "arguments": unsafe},
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
            process_env["HOME"] = str(self.toolchains.home)
            process_env["PATH"] = os.pathsep.join(
                [
                    str(Path(resolved_program).parent.resolve()),
                    process_env.get("PATH", ""),
                ]
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
        payload = command_payload(
            managed, int(args.get("max_output_bytes", 65_536))
        )
        payload["program"] = resolved_program
        payload["argv"] = argv
        payload["shell"] = False
        return self._format_command_payload(payload, args)

    @staticmethod
    def _shell_program_names(cmd: str) -> list[str]:
        builtins = {
            ".", ":", "alias", "bg", "break", "cd", "continue", "echo",
            "eval", "exec", "exit", "export", "false", "fg", "getopts",
            "hash", "jobs", "printf", "pwd", "read", "readonly", "return",
            "set", "shift", "test", "times", "trap", "true", "type",
            "ulimit", "umask", "unalias", "unset", "wait",
        }
        prefixes = {
            "do", "done", "elif", "else", "fi", "if", "then", "while", "until"
        }
        control_heads = {
            "case", "esac", "for", "function", "in", "select", "{", "}"
        }
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
        env_overrides = {
            str(key): str(value) for key, value in dict(args.get("env") or {}).items()
        }
        self._validate_command(cmd, env_overrides, timeout_ms)
        cwd = self.workspace.existing(
            str(args.get("cwd") or args.get("workdir", "."))
        ).absolute
        if not cwd.is_dir():
            raise ToolError(
                "NOT_DIRECTORY", "command workdir is not a directory", "filesystem"
            )
        privileged = self.permission_mode == "dangerous" or self._permission_granted(
            "privileged_executable"
        )
        command_roots: list[Path] = []
        command_bins: list[Path] = []
        for program in self._shell_program_names(cmd):
            program_key = os.path.normcase(program.strip())
            if not privileged and program_key in self._privileged_program_misses:
                raise ToolError(
                    "EXECUTABLE_NOT_FOUND",
                    f"宿主机用户环境已查询过，仍未找到 {program}；本次 MCP Server 会话不再重复请求环境权限。",
                    "process",
                    False,
                    {
                        "program": program,
                        "elevated_lookup": True,
                        "cached_miss": True,
                        "hint": "安装或调整工具环境后，请重启当前 MCP Server 再重新检测。",
                    },
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
                self._privileged_program_misses.add(program_key)
                raise ToolError(
                    "EXECUTABLE_NOT_FOUND",
                    f"program is not available in either the sandbox or elevated user environment: {program}",
                    "process",
                    False,
                    {"program": program, "elevated_lookup": True},
                )
            if privileged:
                self._privileged_program_misses.discard(program_key)
                root = self.toolchains.readable_root_for_program(
                    program,
                    resolved,
                    privileged=True,
                )
                if root not in command_roots:
                    command_roots.append(root)
                bin_dir = Path(resolved).parent.resolve()
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
            command_env["HOME"] = str(self.toolchains.home)
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
        managed = self.commands.write(
            str(args["command_id"]), str(args.get("chars", ""))
        )
        self.commands.wait(managed, int(args.get("yield_time_ms", 10_000)))
        return self._format_command_payload(
            command_payload(managed, int(args.get("max_output_bytes", 65_536))),
            args,
        )

    def kill_command(self, args: dict[str, Any]) -> dict[str, Any]:
        command_id = str(args["command_id"])
        status = self.commands.terminate(
            command_id,
            str(args.get("signal", "TERM")),
            wait_ms=int(args.get("wait_ms", 5_000)),
            kill_wait_ms=int(args.get("kill_wait_ms", 2_000)),
        )
        managed = self.commands.get(command_id)
        payload = command_payload(
            managed, int(args.get("max_output_bytes", 65_536))
        )
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
        state = (
            f"exit {exit_code}"
            if exit_code is not None
            else str(payload.get("status", "running"))
        )
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
            preview, preview_truncated = truncate_text(
                "\n".join(sections),
                int(args.get("preview_bytes", 4_096)),
            )
            compact["preview"] = preview
            compact["preview_truncated"] = preview_truncated
            compact["truncated"] = bool(
                compact.get("truncated") or preview_truncated
            )
        return compact

    def read_output(self, args: dict[str, Any]) -> dict[str, Any]:
        ref = str(args["output_ref"])
        match = re.fullmatch(r"command:([A-Za-z0-9_-]+):(stdout|stderr)", ref)
        if not match:
            raise ToolError(
                "INVALID_OUTPUT_REF",
                "output_ref must be command:<id>:stdout|stderr",
                "validation",
            )
        command = self.commands.get(match.group(1))
        ref_stream = match.group(2)
        stream = str(args.get("stream") or ref_stream)
        if stream != ref_stream:
            raise ToolError(
                "INVALID_ARGUMENT",
                "stream does not match output_ref",
                "validation",
            )
        return dict(
            self.commands.output(
                command,
                stream,
                int(args.get("offset", 0)),
                int(args.get("limit", 4_096)),
            )
        )

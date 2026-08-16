"""Bounded subprocess lifecycle used by exec_command tools.

The manager keeps command state independent from an individual MCP request so
long-running commands can be polled, written to, read back, or terminated by a
later request.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from .errors import ToolError


MAX_ACTIVE_COMMANDS = 16
MAX_RETAINED_COMMANDS = 32
STREAM_LIMIT_BYTES = 512 * 1024
STREAM_HEAD_BYTES = 64 * 1024


class OutputBuffer:
    """Keep the beginning and most recent tail of a potentially huge stream."""

    def __init__(self, limit: int = STREAM_LIMIT_BYTES, head: int = STREAM_HEAD_BYTES):
        self.limit = limit
        self.head_limit = min(head, limit)
        self._head = bytearray()
        self._tail = bytearray()
        self._total = 0
        self._lock = threading.RLock()

    def append(self, text: str) -> None:
        data = text.encode("utf-8", "replace")
        with self._lock:
            self._total += len(data)
            remaining = max(0, self.head_limit - len(self._head))
            if remaining:
                self._head.extend(data[:remaining])
                data = data[remaining:]
            tail_limit = self.limit - self.head_limit
            if tail_limit > 0 and data:
                self._tail.extend(data)
                if len(self._tail) > tail_limit:
                    del self._tail[: len(self._tail) - tail_limit]

    def bytes(self) -> tuple[bytes, int]:
        with self._lock:
            retained = bytes(self._head + self._tail)
            evicted = max(0, self._total - len(retained))
            return retained, evicted

    def text(self) -> tuple[str, int]:
        raw, evicted = self.bytes()
        return raw.decode("utf-8", "replace"), evicted

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total


@dataclass(slots=True)
class ManagedCommand:
    command_id: str
    command: str
    process: subprocess.Popen[str]
    started_at: float
    timeout_ms: int
    stdout: OutputBuffer = field(default_factory=OutputBuffer)
    stderr: OutputBuffer = field(default_factory=OutputBuffer)
    readers: list[threading.Thread] = field(default_factory=list)
    timed_out: bool = False

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)


class CommandManager:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.runtime_dir = Path(tempfile.mkdtemp(prefix="coding-tools-mcp-"))
        self.home_dir = self.runtime_dir / "home"
        self.tmp_dir = self.runtime_dir / "tmp"
        self.cache_dir = self.runtime_dir / "cache"
        for path in (self.home_dir, self.tmp_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)
        self._commands: OrderedDict[str, ManagedCommand] = OrderedDict()
        self._lock = threading.RLock()

    def _reader(self, command: ManagedCommand, stream: str, handle: IO[str] | None) -> None:
        if handle is None:
            return
        buffer = command.stdout if stream == "stdout" else command.stderr
        try:
            while True:
                chunk = handle.readline()
                if chunk == "":
                    break
                buffer.append(chunk)
        finally:
            try:
                handle.close()
            except OSError:
                pass

    def _watch_timeout(self, command: ManagedCommand) -> None:
        if command.timeout_ms <= 0:
            return
        if command.process.wait(timeout=command.timeout_ms / 1000) is None:
            return

    def _cleanup_retained(self) -> None:
        finished = [item for item in self._commands.values() if item.process.poll() is not None]
        while len(self._commands) > MAX_RETAINED_COMMANDS and finished:
            victim = finished.pop(0)
            self._commands.pop(victim.command_id, None)

    def start(
        self,
        command: str,
        *,
        cwd: Path,
        env: dict[str, str],
        stdin_text: str,
        timeout_ms: int,
    ) -> ManagedCommand:
        with self._lock:
            active = sum(1 for item in self._commands.values() if item.process.poll() is None)
            if active >= MAX_ACTIVE_COMMANDS:
                raise ToolError("TOO_MANY_COMMANDS", "too many commands are already running", "process", True)

            popen_kwargs: dict[str, object] = {
                "cwd": str(cwd),
                "env": env,
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "bufsize": 1,
                "shell": True,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["start_new_session"] = True
                popen_kwargs["executable"] = os.environ.get("SHELL") or "/bin/sh"

            try:
                process = subprocess.Popen(command, **popen_kwargs)  # type: ignore[arg-type]
            except OSError as exc:
                raise ToolError("COMMAND_START_FAILED", "failed to start command", "process", True, {"error": str(exc)}) from exc

            managed = ManagedCommand(uuid.uuid4().hex, command, process, time.monotonic(), timeout_ms)
            for stream_name, handle in (("stdout", process.stdout), ("stderr", process.stderr)):
                thread = threading.Thread(target=self._reader, args=(managed, stream_name, handle), daemon=True)
                managed.readers.append(thread)
                thread.start()
            self._commands[managed.command_id] = managed
            self._cleanup_retained()

        if stdin_text and process.stdin is not None:
            try:
                process.stdin.write(stdin_text)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        return managed

    def get(self, command_id: str) -> ManagedCommand:
        with self._lock:
            command = self._commands.get(command_id)
        if command is None:
            raise ToolError("COMMAND_NOT_FOUND", f"unknown or expired command_id: {command_id}", "process", False)
        return command

    def wait(self, command: ManagedCommand, yield_ms: int) -> None:
        remaining_timeout = max(0, command.timeout_ms - command.elapsed_ms())
        wait_ms = min(max(0, yield_ms), remaining_timeout if remaining_timeout else max(0, yield_ms))
        try:
            command.process.wait(timeout=wait_ms / 1000 if wait_ms else 0)
        except subprocess.TimeoutExpired:
            pass
        if command.process.poll() is None and command.timeout_ms and command.elapsed_ms() >= command.timeout_ms:
            command.timed_out = True
            self.terminate(command.command_id, "TERM", wait_ms=500, kill_wait_ms=500)
        if command.process.poll() is not None and command.process.stdin is not None:
            try:
                command.process.stdin.close()
            except OSError:
                pass

    def write(self, command_id: str, chars: str) -> ManagedCommand:
        command = self.get(command_id)
        if chars and command.process.poll() is None and command.process.stdin is not None:
            try:
                command.process.stdin.write(chars)
                command.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise ToolError("STDIN_CLOSED", "command stdin is closed", "process", False, {"error": str(exc)}) from exc
        return command

    def terminate(self, command_id: str, signal_name: str, *, wait_ms: int, kill_wait_ms: int) -> str:
        command = self.get(command_id)
        process = command.process
        if process.poll() is not None:
            return "exited"
        sig = {"TERM": signal.SIGTERM, "INT": signal.SIGINT, "KILL": signal.SIGKILL}.get(signal_name, signal.SIGTERM)
        try:
            if os.name == "nt":
                if signal_name == "KILL":
                    process.kill()
                else:
                    process.terminate()
            else:
                os.killpg(process.pid, sig)
        except (ProcessLookupError, OSError):
            pass
        try:
            process.wait(timeout=max(0, wait_ms) / 1000)
            return "terminated"
        except subprocess.TimeoutExpired:
            if signal_name != "KILL":
                try:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    process.wait(timeout=max(0, kill_wait_ms) / 1000)
                    return "killed"
                except subprocess.TimeoutExpired:
                    return "terminating"
            return "terminating"

    def output(self, command: ManagedCommand, stream: str, offset: int, limit: int) -> dict[str, object]:
        buffer = command.stdout if stream == "stdout" else command.stderr
        data, evicted = buffer.bytes()
        # The retained buffer may no longer begin at logical offset 0. Expose
        # that fact so clients do not mistake a rolling tail for contiguous data.
        logical_start = evicted
        relative = max(0, offset - logical_start)
        chunk = data[relative : relative + limit]
        return {
            "output_ref": f"command:{command.command_id}:{stream}",
            "stream": stream,
            "offset": offset,
            "logical_start": logical_start,
            "data": chunk.decode("utf-8", "replace"),
            "bytes_returned": len(chunk),
            "total_bytes": buffer.total_bytes,
            "evicted_gap_bytes": evicted,
            "next_offset": offset + len(chunk),
            "eof": command.process.poll() is not None and relative + len(chunk) >= len(data),
        }

    def close(self) -> None:
        with self._lock:
            commands = list(self._commands.values())
        for command in commands:
            if command.process.poll() is None:
                try:
                    self.terminate(command.command_id, "TERM", wait_ms=300, kill_wait_ms=300)
                except ToolError:
                    pass


def bounded_text(value: str, limit: int) -> tuple[str, bool]:
    raw = value.encode("utf-8", "replace")
    if len(raw) <= limit:
        return value, False
    return raw[:limit].decode("utf-8", "ignore"), True


def command_payload(command: ManagedCommand, max_bytes: int) -> dict[str, object]:
    stdout, stdout_evicted = command.stdout.text()
    stderr, stderr_evicted = command.stderr.text()
    stdout, stdout_cut = bounded_text(stdout, max_bytes)
    stderr, stderr_cut = bounded_text(stderr, max_bytes)
    exit_code = command.process.poll()
    return {
        "command_id": command.command_id,
        "status": "running" if exit_code is None else "exited",
        "exit_code": exit_code,
        "elapsed_ms": command.elapsed_ms(),
        "timed_out": command.timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_ref": f"command:{command.command_id}:stdout",
        "stderr_ref": f"command:{command.command_id}:stderr",
        "stdout_evicted_bytes": stdout_evicted,
        "stderr_evicted_bytes": stderr_evicted,
        "truncated": stdout_cut or stderr_cut or bool(stdout_evicted or stderr_evicted),
    }

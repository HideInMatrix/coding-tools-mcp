from __future__ import annotations

import queue
import subprocess
import threading
import time
from pathlib import Path

from ..executables import resolve_executable
from ..process_utils import LogCallback, hidden_process_kwargs, stop_process
from .base import NetworkProvider


class ProcessNetworkProvider(NetworkProvider):
    """Reusable lifecycle for network providers backed by a child process."""

    process_name = "network provider"

    def __init__(self, log: LogCallback):
        self._log = log
        self.process: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str] = queue.Queue()

    @property
    def is_running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    @property
    def exit_code(self) -> int | None:
        return self.process.returncode if self.process is not None else None

    def resolve_executable(self, configured: str, default_name: str) -> Path:
        candidate = resolve_executable(default_name, configured=configured)
        self._log(
            f"{default_name} 客户端: {candidate.path} "
            f"(版本 {candidate.version}, 来源 {candidate.source_label})"
        )
        if candidate.warning:
            self._log(f"{default_name} 检测提示: {candidate.warning}")
        return candidate.path

    def spawn(self, command: list[str], *, prefix: str) -> None:
        self._log("启动网络进程: " + " ".join(command[:2]) + (" ..." if len(command) > 2 else ""))
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **hidden_process_kwargs(),
        )
        if self.process.stdout is None:
            raise RuntimeError(f"无法读取 {self.process_name} 输出。")
        threading.Thread(target=self._read_output, args=(prefix,), daemon=True).start()

    def _read_output(self, prefix: str) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in iter(process.stdout.readline, ""):
            text = line.rstrip()
            if not text:
                continue
            self._lines.put(text)
            self._log(f"[{prefix}] {text}")

    def wait_for_line(self, predicate, *, timeout: float, description: str) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is None:
                raise RuntimeError(f"{self.process_name} 未启动。")
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"{self.process_name} 已退出，退出码: {self.process.returncode}"
                )
            try:
                line = self._lines.get(timeout=0.4)
            except queue.Empty:
                continue
            if predicate(line):
                return line
        raise RuntimeError(f"等待 {description} 超时。")

    def wait_until_stable(self, *, timeout: float = 1.5) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is None:
                raise RuntimeError(f"{self.process_name} 未启动。")
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"{self.process_name} 启动失败，退出码: {self.process.returncode}"
                )
            time.sleep(0.1)

    def stop(self) -> None:
        stop_process(self.process, name=self.process_name, log=self._log)
        self.process = None

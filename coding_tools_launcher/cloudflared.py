from __future__ import annotations

import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from .process_utils import LogCallback, hidden_process_kwargs, stop_process
from .resources import resolve_cloudflared


TUNNEL_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


class CloudflaredTunnel:
    def __init__(self, log: LogCallback):
        self._log = log
        self.process: subprocess.Popen[str] | None = None
        self.url = ""
        self.binary_path: Path | None = None
        self._lines: queue.Queue[str] = queue.Queue()

    def start(self, host: str, port: int, timeout: float = 60.0) -> str:
        self.binary_path = resolve_cloudflared()
        command = [
            str(self.binary_path),
            "tunnel",
            "--protocol",
            "http2",
            "--url",
            f"http://{host}:{port}",
        ]
        self._log(f"启动 Cloudflare Quick Tunnel: {self.binary_path}")
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **hidden_process_kwargs(),
        )
        if self.process.stdout is None:
            raise RuntimeError("无法读取 cloudflared 输出。")

        threading.Thread(target=self._read_output, daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    "cloudflared 在生成 Tunnel URL 前退出，"
                    f"退出码: {self.process.returncode}"
                )
            try:
                line = self._lines.get(timeout=0.5)
            except queue.Empty:
                continue
            match = TUNNEL_URL_PATTERN.search(line)
            if match:
                self.url = match.group(0)
                self._log(f"Quick Tunnel URL: {self.url}")
                return self.url
        raise RuntimeError("等待 Cloudflare Quick Tunnel URL 超时。")

    def _read_output(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in iter(process.stdout.readline, ""):
            text = line.rstrip()
            if not text:
                continue
            self._lines.put(text)
            self._log(f"[cloudflared] {text}")

    def stop(self) -> None:
        stop_process(self.process, name="cloudflared", log=self._log)

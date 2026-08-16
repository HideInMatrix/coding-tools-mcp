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

    def start(
        self,
        host: str,
        port: int,
        *,
        public_url: str = "",
        tunnel_token: str = "",
        timeout: float = 60.0,
    ) -> str:
        self.binary_path = resolve_cloudflared()

        if public_url:
            if not tunnel_token:
                raise RuntimeError(
                    "使用固定 Public URL 时必须配置 Cloudflare Named Tunnel Token。"
                    "请在 Cloudflare Tunnel 的安装命令中复制 --token 后面的值。"
                )
            return self._start_named_tunnel(
                host,
                port,
                public_url=public_url,
                tunnel_token=tunnel_token,
                timeout=timeout,
            )

        return self._start_quick_tunnel(host, port, timeout=timeout)

    def _start_quick_tunnel(self, host: str, port: int, *, timeout: float) -> str:
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

    def _start_named_tunnel(
        self,
        host: str,
        port: int,
        *,
        public_url: str,
        tunnel_token: str,
        timeout: float,
    ) -> str:
        command = [
            str(self.binary_path),
            "tunnel",
            "--protocol",
            "http2",
            "run",
            "--token",
            tunnel_token,
        ]
        self._log(f"启动 Cloudflare Named Tunnel: {self.binary_path}")
        self._log(f"固定 Public URL: {public_url}")
        self._log(
            "Cloudflare Published Application 应指向: "
            f"http://{host}:{port}"
        )
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
                    "Cloudflare Named Tunnel 启动失败，"
                    f"退出码: {self.process.returncode}"
                )
            try:
                line = self._lines.get(timeout=0.5)
            except queue.Empty:
                continue
            lowered = line.lower()
            if "registered tunnel connection" in lowered:
                self.url = public_url
                self._log("Named Tunnel 已连接 Cloudflare Edge。")
                return self.url
        raise RuntimeError("等待 Cloudflare Named Tunnel 建立连接超时。")

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

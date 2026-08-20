from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from collections.abc import Callable


LogCallback = Callable[[str], None]


def check_port_available(host: str, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError as exc:
        raise RuntimeError(
            f"端口 {host}:{port} 已被占用，请更换端口或关闭占用程序。"
        ) from exc
    finally:
        sock.close()


def hidden_process_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def stop_process(
    process: subprocess.Popen[str] | None,
    *,
    name: str,
    log: LogCallback | None = None,
) -> None:
    if process is None or process.poll() is not None:
        return
    if log:
        log(f"正在停止 {name}...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if log:
            log(f"{name} 未正常退出，正在强制结束...")
        process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def forward_process_output(
    process: subprocess.Popen[str], *, prefix: str, log: LogCallback
) -> threading.Thread | None:
    if process.stdout is None:
        return None

    def reader() -> None:
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            text = line.rstrip()
            if text:
                log(f"[{prefix}] {text}")

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return thread


def wait_for_tcp_port(
    host: str,
    port: int,
    *,
    process: subprocess.Popen[str],
    timeout: float = 12.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Agent Runtime 启动失败，退出码: {process.returncode}"
            )
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.15)
    raise RuntimeError(f"等待 Agent Runtime 监听 {host}:{port} 超时。")

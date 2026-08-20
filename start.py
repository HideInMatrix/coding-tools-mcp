#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import IO


# ============================================================
# 默认配置
# ============================================================

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8234

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

TUNNEL_URL_PATTERN = re.compile(
    r"https://[a-zA-Z0-9-]+\.trycloudflare\.com"
)

REQUIRED_ENV = (
    "AGENT_RUNTIME_OAUTH_PASSWORD",
)


# ============================================================
# 全局进程
# ============================================================

cloudflared_process: subprocess.Popen[str] | None = None
mcp_process: subprocess.Popen[str] | None = None


# ============================================================
# 参数
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="启动 MicroMatrix Workbench Agent Runtime + Cloudflare Quick Tunnel"
    )

    parser.add_argument(
        "workspace",
        nargs="?",
        default=".",
        help="MCP workspace，默认使用当前目录",
    )

    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"MCP 本地监听地址，默认: {DEFAULT_HOST}",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"MCP 本地监听端口，默认: {DEFAULT_PORT}",
    )

    return parser.parse_args()


# ============================================================
# .env
# ============================================================

def load_env(path: Path) -> dict[str, str]:
    """
    读取简单的 KEY=VALUE .env 文件。

    支持：
        KEY=value
        KEY="value"
        KEY='value'

    忽略：
        空行
        # 注释
    """

    if not path.exists():
        raise FileNotFoundError(
            f"找不到配置文件: {path}"
        )

    result: dict[str, str] = {}

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if "=" not in line:
            raise ValueError(
                f"{path}:{line_number} 配置格式错误，"
                f"应为 KEY=VALUE"
            )

        key, value = line.split("=", 1)

        key = key.strip()
        value = value.strip()

        if not key:
            raise ValueError(
                f"{path}:{line_number} KEY 不能为空"
            )

        # 去掉成对引号
        if len(value) >= 2:
            if (
                value.startswith('"')
                and value.endswith('"')
            ) or (
                value.startswith("'")
                and value.endswith("'")
            ):
                value = value[1:-1]

        result[key] = value

    return result


# ============================================================
# 环境检查
# ============================================================

def check_command(command: str) -> None:
    """
    检查命令是否存在。
    """

    from shutil import which

    if which(command) is None:
        raise RuntimeError(
            f"找不到命令 `{command}`。\n"
            f"请先安装并确保它位于 PATH 中。"
        )


def check_port_available(
    host: str,
    port: int,
) -> None:
    """
    检查本地 TCP 端口是否已经被占用。
    """

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    )

    try:
        sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1,
        )

        sock.bind((host, port))

    except OSError as exc:
        raise RuntimeError(
            f"端口 {host}:{port} 已被占用，"
            f"请更换端口或关闭占用该端口的程序。"
        ) from exc

    finally:
        sock.close()


# ============================================================
# Workspace
# ============================================================

def resolve_workspace(path: str) -> Path:
    workspace = (
        Path(path)
        .expanduser()
        .resolve()
    )

    if not workspace.exists():
        raise RuntimeError(
            f"Workspace 不存在:\n{workspace}"
        )

    if not workspace.is_dir():
        raise RuntimeError(
            f"Workspace 不是目录:\n{workspace}"
        )

    return workspace


# ============================================================
# Cloudflared
# ============================================================

def start_cloudflared(
    host: str,
    port: int,
) -> tuple[
    subprocess.Popen[str],
    str,
]:
    """
    启动 Cloudflare Quick Tunnel。

    强制使用 HTTP/2：
        --protocol http2

    因为当前网络环境：
        UDP 7844 不通
        TCP 7844 正常
    """

    command = [
        "cloudflared",
        "tunnel",
        "--protocol",
        "http2",
        "--url",
        f"http://{host}:{port}",
    ]

    print("启动 cloudflared ...")
    print(
        "Command:",
        " ".join(command),
    )
    print()

    process: subprocess.Popen[str] = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert process.stdout is not None

    tunnel_url: str | None = None

    start_time = time.monotonic()

    while True:
        if (
            time.monotonic() - start_time
            > 60
        ):
            process.terminate()

            raise RuntimeError(
                "等待 Cloudflare Tunnel URL 超时。"
            )

        line = process.stdout.readline()

        if line:
            print(
                "[cloudflared]",
                line,
                end="",
            )

            match = TUNNEL_URL_PATTERN.search(
                line
            )

            if match:
                tunnel_url = match.group(0)
                break

        elif process.poll() is not None:
            raise RuntimeError(
                "cloudflared 在生成 Tunnel URL 之前退出，"
                f"退出码: {process.returncode}"
            )

    assert tunnel_url is not None

    return process, tunnel_url


# ============================================================
# 等待 Tunnel 真正连接
# ============================================================

def wait_for_cloudflared_connection(
    process: subprocess.Popen[str],
    timeout: int = 30,
) -> None:
    """
    等待 cloudflared 建立连接。

    注意：
    Quick Tunnel 生成 URL 不等于 Tunnel 已经连接成功。
    """

    if process.poll() is not None:
        raise RuntimeError(
            "cloudflared 已退出，无法建立 Tunnel。"
        )

    print()
    print(
        "等待 Cloudflare Tunnel 建立连接..."
    )

    # 这里不主动读取 stdout，
    # 主输出循环稍后会持续读取。
    # 给 cloudflared 一点时间完成 HTTP/2 连接。
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "cloudflared 在连接 Cloudflare Edge 时退出。"
            )

        time.sleep(0.5)

    print(
        "Cloudflare Tunnel 进程仍在运行。"
    )


# ============================================================
# 启动 MCP
# ============================================================

def start_mcp(
    workspace: Path,
    host: str,
    port: int,
    env: dict[str, str],
) -> subprocess.Popen[str]:
    command = [
        sys.executable,
        "-m",
        "agent_runtime",
        "--workspace",
        str(workspace),
        "--host",
        host,
        "--port",
        str(port),
        "--oauth-mode",
    ]

    print()
    print("启动 Agent Runtime ...")
    print(
        "Workspace:",
        workspace,
    )
    print(
        "Local MCP:",
        f"http://{host}:{port}/mcp",
    )
    print()

    process: subprocess.Popen[str] = subprocess.Popen(
        command,
        env=env,
    )

    time.sleep(2)

    if process.poll() is not None:
        raise RuntimeError(
            "Agent Runtime 启动失败，"
            f"退出码: {process.returncode}"
        )

    return process


# ============================================================
# 信号处理
# ============================================================

def stop_process(
    process: subprocess.Popen[str] | None,
    name: str,
) -> None:
    if process is None:
        return

    if process.poll() is not None:
        return

    print(
        f"正在停止 {name}..."
    )

    try:
        process.terminate()

        process.wait(
            timeout=5
        )

    except subprocess.TimeoutExpired:
        print(
            f"{name} 未正常退出，强制结束..."
        )

        process.kill()

        try:
            process.wait(
                timeout=2
            )
        except subprocess.TimeoutExpired:
            pass


def handle_signal(
    signum: int,
    frame: object,
) -> None:
    print()
    print(
        f"收到信号 {signum}，正在关闭服务..."
    )

    stop_process(
        mcp_process,
        "agent-runtime",
    )

    stop_process(
        cloudflared_process,
        "cloudflared",
    )

    sys.exit(0)


# ============================================================
# Cloudflared 日志转发线程
# ============================================================

def forward_cloudflared_logs(
    process: subprocess.Popen[str],
) -> None:
    """
    注意：

    start_cloudflared() 在获取 Tunnel URL 后，
    stdout 中还会继续产生日志。

    这里使用后台线程持续读取，避免 stdout buffer 堵塞。
    """

    import threading

    def reader(
        stream: IO[str],
    ) -> None:
        for line in iter(
            stream.readline,
            "",
        ):
            print(
                "[cloudflared]",
                line,
                end="",
            )

    if process.stdout is None:
        return

    thread = threading.Thread(
        target=reader,
        args=(process.stdout,),
        daemon=True,
    )

    thread.start()


# ============================================================
# 主函数
# ============================================================

def main() -> int:
    global cloudflared_process
    global mcp_process

    args = parse_args()

    # --------------------------------------------------------
    # 1. Workspace
    # --------------------------------------------------------

    workspace = resolve_workspace(
        args.workspace
    )

    # --------------------------------------------------------
    # 2. 端口
    # --------------------------------------------------------

    if not (
        1 <= args.port <= 65535
    ):
        raise RuntimeError(
            f"无效端口: {args.port}"
        )

    check_port_available(
        args.host,
        args.port,
    )

    # --------------------------------------------------------
    # 3. 检查命令
    # --------------------------------------------------------

    check_command(
        "cloudflared"
    )

    # --------------------------------------------------------
    # 4. 读取 .env
    # --------------------------------------------------------

    config = load_env(
        ENV_FILE
    )

    missing = [
        key
        for key in REQUIRED_ENV
        if not config.get(key)
    ]

    if missing:
        raise RuntimeError(
            "缺少 OAuth 配置:\n"
            + "\n".join(
                f"  - {key}"
                for key in missing
            )
        )

    # --------------------------------------------------------
    # 5. 当前 shell 环境 + .env
    # --------------------------------------------------------

    env = os.environ.copy()
    env.update(config)
    env.pop("AGENT_RUNTIME_OAUTH_CLIENT_ID", None)
    env.pop("AGENT_RUNTIME_OAUTH_CLIENT_SECRET", None)

    # --------------------------------------------------------
    # 6. 先启动 Cloudflare Tunnel
    # --------------------------------------------------------

    cloudflared_process, tunnel_url = (
        start_cloudflared(
            args.host,
            args.port,
        )
    )

    # --------------------------------------------------------
    # 7. 关键：
    #    把真实 Tunnel URL 注入 MCP
    # --------------------------------------------------------

    env[
        "AGENT_RUNTIME_SERVER_URL"
    ] = tunnel_url

    # --------------------------------------------------------
    # 8. 转发 cloudflared 后续日志
    # --------------------------------------------------------

    forward_cloudflared_logs(
        cloudflared_process
    )

    # --------------------------------------------------------
    # 9. 等待一下
    # --------------------------------------------------------

    wait_for_cloudflared_connection(
        cloudflared_process
    )

    # --------------------------------------------------------
    # 10. 启动 MCP
    # --------------------------------------------------------

    mcp_process = start_mcp(
        workspace=workspace,
        host=args.host,
        port=args.port,
        env=env,
    )

    # --------------------------------------------------------
    # 11. 输出最终信息
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("Agent Runtime 已启动")
    print("=" * 70)

    print(
        f"Workspace : {workspace}"
    )

    print(
        f"Local MCP : "
        f"http://{args.host}:{args.port}/mcp"
    )

    print(
        f"Remote MCP: "
        f"{tunnel_url}/mcp"
    )

    print(
        f"OAuth URL : "
        f"{tunnel_url}"
    )

    print()
    print(
        "OpenAI MCP Server 地址:"
    )

    print(
        f"  {tunnel_url}/mcp"
    )

    print()
    print(
        "按 Ctrl+C 停止服务。"
    )

    print("=" * 70)
    print()

    # --------------------------------------------------------
    # 12. 主循环
    # --------------------------------------------------------

    while True:
        if mcp_process.poll() is not None:
            print(
                "Agent Runtime 已退出，"
                f"退出码: {mcp_process.returncode}"
            )
            break

        if cloudflared_process.poll() is not None:
            print(
                "cloudflared 已退出，"
                f"退出码: "
                f"{cloudflared_process.returncode}"
            )
            break

        time.sleep(1)

    return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    signal.signal(
        signal.SIGINT,
        handle_signal,
    )

    signal.signal(
        signal.SIGTERM,
        handle_signal,
    )

    try:
        raise SystemExit(
            main()
        )

    except KeyboardInterrupt:
        print()
        print(
            "正在退出..."
        )

        stop_process(
            mcp_process,
            "agent-runtime",
        )

        stop_process(
            cloudflared_process,
            "cloudflared",
        )

        raise SystemExit(0)

    except Exception as exc:
        print()
        print(
            "启动失败:"
        )
        print(
            f"  {exc}"
        )

        stop_process(
            mcp_process,
            "agent-runtime",
        )

        stop_process(
            cloudflared_process,
            "cloudflared",
        )

        raise SystemExit(1)
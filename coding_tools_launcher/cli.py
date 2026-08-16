from __future__ import annotations

import argparse
import signal
from pathlib import Path

from .config import DEFAULT_HOST, DEFAULT_PORT, LaunchConfig, load_env_file
from .launcher import MCPLauncher


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="启动 coding-tools-mcp + Cloudflare Quick Tunnel"
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
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"OAuth 配置文件，默认: {DEFAULT_ENV_FILE}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = load_env_file(args.env_file.expanduser().resolve())
    config = LaunchConfig.from_env(
        workspace=Path(args.workspace),
        env=env,
        host=args.host,
        port=args.port,
    )
    launcher = MCPLauncher(log=print)

    def shutdown(_signum: int, _frame: object) -> None:
        print("\n正在关闭服务...")
        launcher.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        info = launcher.start(config)
        print()
        print("=" * 70)
        print("coding-tools-mcp 已启动")
        print("=" * 70)
        print(f"Workspace : {info.workspace}")
        print(f"Local MCP : {info.local_mcp_url}")
        print(f"Tunnel    : {info.tunnel_url}")
        print(f"Public MCP: {info.public_mcp_url}")
        print(f"OAuth URL : {info.public_base_url}")
        print(f"URL Mode  : {info.url_mode}")
        print()
        print("OpenAI MCP Server 地址:")
        print(f"  {info.public_mcp_url}")
        print()
        print("按 Ctrl+C 停止服务。")
        print("=" * 70)
        launcher.wait()
        return 1 if launcher.exit_reason else 0
    finally:
        launcher.stop()

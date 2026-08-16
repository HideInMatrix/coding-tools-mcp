from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .config import LaunchConfig
from .process_utils import (
    LogCallback,
    forward_process_output,
    hidden_process_kwargs,
    stop_process,
    wait_for_tcp_port,
)
from .resources import PROJECT_ROOT, is_frozen


INTERNAL_MCP_FLAG = "--internal-mcp-server"


def _mcp_arguments(config: LaunchConfig) -> list[str]:
    return [
        "--workspace",
        str(config.workspace),
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--oauth-mode",
    ]


def build_mcp_command(config: LaunchConfig) -> list[str]:
    arguments = _mcp_arguments(config)
    if is_frozen():
        return [sys.executable, INTERNAL_MCP_FLAG, *arguments]

    executable = shutil.which("coding-tools-mcp")
    if executable:
        return [executable, *arguments]

    name = "coding-tools-mcp.exe" if os.name == "nt" else "coding-tools-mcp"
    candidate = (
        PROJECT_ROOT
        / ".venv"
        / ("Scripts" if os.name == "nt" else "bin")
        / name
    )
    if candidate.is_file():
        return [str(candidate), *arguments]
    raise RuntimeError(
        "开发环境未找到 coding-tools-mcp。请先安装项目依赖；"
        "正式桌面安装包会内置该依赖。"
    )


def run_internal_mcp_server(arguments: list[str]) -> int:
    from coding_tools_mcp.server import main as server_main

    old_argv = sys.argv[:]
    try:
        sys.argv = ["coding-tools-mcp", *arguments]
        result = server_main()
        return int(result or 0)
    finally:
        sys.argv = old_argv


class MCPServerProcess:
    def __init__(self, log: LogCallback):
        self._log = log
        self.process: subprocess.Popen[str] | None = None

    def start(self, config: LaunchConfig, env: dict[str, str]) -> None:
        command = build_mcp_command(config)
        self._log(f"启动 coding-tools-mcp，Workspace: {config.workspace}")
        self.process = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **hidden_process_kwargs(),
        )
        forward_process_output(self.process, prefix="mcp", log=self._log)
        wait_for_tcp_port(
            config.host,
            config.port,
            process=self.process,
        )

    def stop(self) -> None:
        stop_process(self.process, name="coding-tools-mcp", log=self._log)

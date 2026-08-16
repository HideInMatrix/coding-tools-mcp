from __future__ import annotations

import os
import threading
import time

from .cloudflared import CloudflaredTunnel
from .config import LaunchConfig, LaunchInfo
from .mcp_process import MCPServerProcess
from .process_utils import LogCallback, check_port_available


class MCPLauncher:
    def __init__(self, log: LogCallback | None = None):
        self._log_callback = log or (lambda _message: None)
        self._lock = threading.RLock()
        self._tunnel = CloudflaredTunnel(self._log)
        self._mcp = MCPServerProcess(self._log)
        self._info: LaunchInfo | None = None
        self._stopping = False
        self._exit_reason = ""

    def _log(self, message: str) -> None:
        self._log_callback(message)

    @property
    def info(self) -> LaunchInfo | None:
        return self._info

    @property
    def exit_reason(self) -> str:
        return self._exit_reason

    @property
    def is_running(self) -> bool:
        tunnel = self._tunnel.process
        mcp = self._mcp.process
        return bool(
            tunnel
            and mcp
            and tunnel.poll() is None
            and mcp.poll() is None
            and not self._stopping
        )

    def start(self, config: LaunchConfig) -> LaunchInfo:
        with self._lock:
            if self.is_running:
                raise RuntimeError("MCP 服务已经在运行。")
            config = config.validated()
            self._stopping = False
            self._exit_reason = ""
            check_port_available(config.host, config.port)
            try:
                tunnel_url = self._tunnel.start(config.host, config.port)
                public_base_url = config.server_url or tunnel_url
                url_mode = "Custom URL" if config.server_url else "Quick Tunnel"
                env = os.environ.copy()
                env.update(
                    {
                        "CODING_TOOLS_MCP_OAUTH_CLIENT_ID": config.oauth_client_id,
                        "CODING_TOOLS_MCP_OAUTH_CLIENT_SECRET": config.oauth_client_secret,
                        "CODING_TOOLS_MCP_OAUTH_PASSWORD": config.oauth_password,
                        "CODING_TOOLS_MCP_SERVER_URL": public_base_url,
                    }
                )
                self._mcp.start(config, env)
                self._info = LaunchInfo(
                    workspace=config.workspace,
                    local_mcp_url=f"http://{config.host}:{config.port}/mcp",
                    tunnel_url=tunnel_url,
                    public_base_url=public_base_url,
                    public_mcp_url=f"{public_base_url}/mcp",
                    url_mode=url_mode,
                )
                self._log(f"MCP 已启动: {self._info.public_mcp_url}")
                threading.Thread(target=self._watch_children, daemon=True).start()
                return self._info
            except Exception:
                self._stop_locked()
                raise

    def _watch_children(self) -> None:
        while True:
            with self._lock:
                if self._stopping:
                    return
                tunnel = self._tunnel.process
                mcp = self._mcp.process
                if tunnel is None or mcp is None:
                    return
                if tunnel.poll() is not None:
                    self._exit_reason = (
                        f"cloudflared 已退出，退出码: {tunnel.returncode}"
                    )
                    self._log(self._exit_reason)
                    self._stop_locked()
                    return
                if mcp.poll() is not None:
                    self._exit_reason = (
                        f"coding-tools-mcp 已退出，退出码: {mcp.returncode}"
                    )
                    self._log(self._exit_reason)
                    self._stop_locked()
                    return
            time.sleep(0.5)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._stopping = True
        self._mcp.stop()
        self._tunnel.stop()
        self._info = None

    def wait(self) -> None:
        while self.is_running:
            time.sleep(0.5)

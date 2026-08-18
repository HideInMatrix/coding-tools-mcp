from __future__ import annotations

import os
import threading
import time

from .config import LaunchConfig, LaunchInfo
from .mcp_process import MCPServerProcess
from .network import NetworkProvider, create_network_provider
from .oauth_persistence import (
    OAUTH_REGISTRY_FILE_ENV,
    OAUTH_TOKEN_SECRET_ENV,
    OAuthPersistence,
    prepare_ephemeral_oauth_persistence,
    prepare_oauth_persistence,
    prepare_server_oauth_persistence,
)
from .process_utils import LogCallback, check_port_available


class MCPLauncher:
    def __init__(self, log: LogCallback | None = None):
        self._log_callback = log or (lambda _message: None)
        self._lock = threading.RLock()
        self._provider: NetworkProvider | None = None
        self._mcp = MCPServerProcess(self._log)
        self._info: LaunchInfo | None = None
        self._oauth_persistence: OAuthPersistence | None = None
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
    def oauth_registry_file(self):
        persistence = self._oauth_persistence
        return persistence.registry_file if persistence is not None else None

    @property
    def oauth_is_ephemeral(self) -> bool:
        persistence = self._oauth_persistence
        return bool(persistence and persistence.ephemeral)

    @property
    def is_running(self) -> bool:
        provider = self._provider
        mcp = self._mcp.process
        return bool(
            provider
            and mcp
            and provider.is_running
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
                self._provider = create_network_provider(
                    config.network.provider,
                    self._log,
                )
                network_info = self._provider.start(config.host, config.port, config.network)
                public_base_url = network_info.public_base_url
                if config.server_id:
                    if config.lifecycle == "ephemeral":
                        oauth_persistence = prepare_ephemeral_oauth_persistence(
                            config.server_id
                        )
                    else:
                        oauth_persistence = prepare_server_oauth_persistence(
                            config.server_id
                        )
                else:
                    oauth_persistence = prepare_oauth_persistence(public_base_url)
                self._oauth_persistence = oauth_persistence
                env = os.environ.copy()
                env.update(
                    {
                        "CODING_TOOLS_MCP_OAUTH_PASSWORD": config.oauth_password,
                        "CODING_TOOLS_MCP_SERVER_URL": public_base_url,
                        OAUTH_TOKEN_SECRET_ENV: oauth_persistence.token_secret_hex,
                        OAUTH_REGISTRY_FILE_ENV: str(oauth_persistence.registry_file),
                    }
                )
                # OAuth clients are always created through RFC 7591 Dynamic
                # Client Registration. Explicitly discard legacy environment
                # variables so old shells/settings cannot silently re-enable
                # preregistered client behaviour.
                env.pop("CODING_TOOLS_MCP_OAUTH_CLIENT_ID", None)
                env.pop("CODING_TOOLS_MCP_OAUTH_CLIENT_SECRET", None)
                if oauth_persistence.ephemeral:
                    self._log(
                        "OAuth 临时 Session 已创建：本次 Quick Tunnel 停止后 client_id 将失效。"
                    )
                else:
                    self._log(
                        "OAuth 状态持久化已启用：动态 client_id 与 token secret 将跨重启保留。"
                    )
                self._mcp.start(config, env)
                self._info = LaunchInfo(
                    workspace=config.workspace,
                    local_mcp_url=f"http://{config.host}:{config.port}/mcp",
                    tunnel_url=public_base_url,
                    public_base_url=public_base_url,
                    public_mcp_url=f"{public_base_url}/mcp",
                    url_mode=network_info.mode_label,
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
                provider = self._provider
                mcp = self._mcp.process
                if provider is None or mcp is None:
                    return
                if not provider.is_running:
                    self._exit_reason = (
                        f"{provider.display_name} 已退出，退出码: {provider.exit_code}"
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
        if self._provider is not None:
            self._provider.stop()
        if self._oauth_persistence is not None:
            self._oauth_persistence.cleanup()
        self._oauth_persistence = None
        self._provider = None
        self._info = None

    def wait(self) -> None:
        while self.is_running:
            time.sleep(0.5)

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from mcp_tools_server.local_permission_broker import (
    BROKER_DIR_ENV,
    BROKER_SECRET_ENV,
    BROKER_SERVER_ID_ENV,
)

from .config import DEFAULT_HOST, DEFAULT_PORT, NetworkConfig
from .gateway_process import (
    GatewayChildProfile,
    GatewayProcessConfig,
    GatewayServerProcess,
)
from .network import NetworkProvider, create_network_provider
from .oauth_persistence import (
    OAUTH_REGISTRY_FILE_ENV,
    OAUTH_TOKEN_SECRET_ENV,
    canonical_oauth_issuer,
)
from .process_utils import LogCallback, check_port_available


@dataclass(frozen=True, slots=True)
class GatewayLaunchConfig:
    network: NetworkConfig
    profiles: tuple[GatewayChildProfile, ...]
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    def validated(self) -> "GatewayLaunchConfig":
        network = self.network.validated()
        host = self.host.strip() or DEFAULT_HOST
        port = int(self.port)
        if not 1 <= port <= 65535:
            raise ValueError(f"无效 Gateway 端口: {port}")
        profiles = tuple(profile.validated() for profile in self.profiles)
        if not profiles:
            raise ValueError("Local MCP Gateway 至少需要一个 Profile。")
        ids: set[str] = set()
        paths: set[str] = set()
        for profile in profiles:
            if profile.server_id in ids:
                raise ValueError(f"重复 Gateway Profile server_id: {profile.server_id}")
            if profile.instance_path in paths:
                raise ValueError(f"重复 Gateway Profile Path: {profile.instance_path}")
            ids.add(profile.server_id)
            paths.add(profile.instance_path)
        if network.public_url:
            parsed = urlsplit(network.public_url)
            if (parsed.path or "").rstrip("/"):
                raise ValueError(
                    "Gateway 固定 Public URL 只能填写 hostname；各 MCP Profile 使用独立 Path。"
                )
        return GatewayLaunchConfig(
            network=network,
            profiles=profiles,
            host=host,
            port=port,
        )


@dataclass(frozen=True, slots=True)
class GatewayProfileLaunchInfo:
    server_id: str
    name: str
    workspace: Path
    instance_path: str
    local_mcp_url: str
    public_mcp_url: str
    oauth_issuer: str
    lifecycle: str


@dataclass(frozen=True, slots=True)
class GatewayLaunchInfo:
    host: str
    port: int
    public_base_url: str
    tunnel_url: str
    url_mode: str
    profiles: tuple[GatewayProfileLaunchInfo, ...]

    def profile(self, server_id: str) -> GatewayProfileLaunchInfo | None:
        target = server_id.strip()
        return next(
            (profile for profile in self.profiles if profile.server_id == target),
            None,
        )


def _ephemeral_network(network: NetworkConfig) -> bool:
    return network.provider in {"cloudflare", "ngrok"} and not network.public_url


def _effective_profiles(
    profiles: tuple[GatewayChildProfile, ...],
    *,
    ephemeral: bool,
) -> tuple[GatewayChildProfile, ...]:
    if not ephemeral:
        return profiles
    return tuple(
        GatewayChildProfile(
            server_id=profile.server_id,
            name=profile.name,
            workspace=profile.workspace,
            oauth_password=profile.oauth_password,
            instance_path=profile.instance_path,
            permission_mode=profile.permission_mode,
            lifecycle="ephemeral",
            allow_network=profile.allow_network,
            enable_view_image=profile.enable_view_image,
        )
        for profile in profiles
    )


class MCPGatewayLauncher:
    """Launch one public network entry and one local multi-profile Gateway."""

    def __init__(
        self,
        log: LogCallback | None = None,
        permission_broker: object | None = None,
    ) -> None:
        self._log_callback = log or (lambda _message: None)
        self._lock = threading.RLock()
        self._provider: NetworkProvider | None = None
        self._gateway = GatewayServerProcess(
            self._log,
            permission_broker=permission_broker,
        )
        self._info: GatewayLaunchInfo | None = None
        self._stopping = False
        self._exit_reason = ""

    def _log(self, message: str) -> None:
        self._log_callback(message)

    @property
    def info(self) -> GatewayLaunchInfo | None:
        return self._info

    @property
    def exit_reason(self) -> str:
        return self._exit_reason

    @property
    def is_running(self) -> bool:
        provider = self._provider
        process = self._gateway.process
        return bool(
            provider
            and process
            and provider.is_running
            and process.poll() is None
            and not self._stopping
        )

    def start(self, config: GatewayLaunchConfig) -> GatewayLaunchInfo:
        with self._lock:
            if self.is_running:
                raise RuntimeError("Local MCP Gateway 已经在运行。")
            validated = config.validated()
            self._stopping = False
            self._exit_reason = ""
            check_port_available(validated.host, validated.port)
            try:
                self._provider = create_network_provider(
                    validated.network.provider,
                    self._log,
                )
                network_info = self._provider.start(
                    validated.host,
                    validated.port,
                    validated.network,
                )
                public_base_url = canonical_oauth_issuer(
                    network_info.public_base_url
                )
                parsed = urlsplit(public_base_url)
                if (parsed.path or "").rstrip("/"):
                    raise RuntimeError(
                        "Gateway Network Provider 返回了带 Path 的 Public URL；"
                        "Gateway 公网入口必须是 hostname 级 URL。"
                    )

                ephemeral = _ephemeral_network(validated.network)
                profiles = _effective_profiles(
                    validated.profiles,
                    ephemeral=ephemeral,
                )
                if ephemeral:
                    self._log(
                        "Gateway 使用临时公网 hostname：所有 Profile OAuth 状态按 ephemeral session 管理。"
                    )

                child_config = GatewayProcessConfig(
                    public_base_url=public_base_url,
                    profiles=profiles,
                    host=validated.host,
                    port=validated.port,
                )
                env = os.environ.copy()
                # Gateway profile secrets/broker identities are instance scoped
                # inside its restricted temporary config. Never let stale
                # single-profile environment variables leak into Gateway Runtime.
                for name in (
                    "CODING_TOOLS_MCP_OAUTH_PASSWORD",
                    "CODING_TOOLS_MCP_SERVER_URL",
                    OAUTH_TOKEN_SECRET_ENV,
                    OAUTH_REGISTRY_FILE_ENV,
                    BROKER_DIR_ENV,
                    BROKER_SECRET_ENV,
                    BROKER_SERVER_ID_ENV,
                ):
                    env.pop(name, None)
                env.pop("CODING_TOOLS_MCP_OAUTH_CLIENT_ID", None)
                env.pop("CODING_TOOLS_MCP_OAUTH_CLIENT_SECRET", None)

                self._gateway.start(child_config, env)
                profile_info = tuple(
                    GatewayProfileLaunchInfo(
                        server_id=profile.server_id,
                        name=profile.name,
                        workspace=profile.workspace,
                        instance_path=profile.instance_path,
                        local_mcp_url=(
                            f"http://{validated.host}:{validated.port}"
                            f"{profile.instance_path}/mcp"
                        ),
                        public_mcp_url=(
                            f"{public_base_url}{profile.instance_path}/mcp"
                        ),
                        oauth_issuer=f"{public_base_url}{profile.instance_path}",
                        lifecycle=profile.lifecycle,
                    )
                    for profile in profiles
                )
                self._info = GatewayLaunchInfo(
                    host=validated.host,
                    port=validated.port,
                    public_base_url=public_base_url,
                    tunnel_url=public_base_url,
                    url_mode=network_info.mode_label,
                    profiles=profile_info,
                )
                self._log(
                    f"Local MCP Gateway 已启动: {public_base_url}，"
                    f"Profiles: {len(profile_info)}"
                )
                for profile in profile_info:
                    self._log(
                        f"Gateway Profile [{profile.name}]: {profile.public_mcp_url}"
                    )
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
                process = self._gateway.process
                if provider is None or process is None:
                    return
                if not provider.is_running:
                    self._exit_reason = (
                        f"{provider.display_name} 已退出，退出码: {provider.exit_code}"
                    )
                    self._log(self._exit_reason)
                    self._stop_locked()
                    return
                if process.poll() is not None:
                    self._exit_reason = (
                        f"coding-tools-mcp-gateway 已退出，退出码: {process.returncode}"
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
        self._gateway.stop()
        if self._provider is not None:
            self._provider.stop()
        self._provider = None
        self._info = None

    def wait(self) -> None:
        while self.is_running:
            time.sleep(0.5)


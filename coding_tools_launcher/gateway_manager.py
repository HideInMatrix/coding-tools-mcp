from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .gateway_launcher import GatewayLaunchConfig, GatewayLaunchInfo, MCPGatewayLauncher
from .gateway_profiles import GatewayProfileStore, MCPGatewayProfile
from .process_utils import LogCallback

if TYPE_CHECKING:
    from .permission_broker import DesktopPermissionBroker


@dataclass(frozen=True, slots=True)
class ManagedGatewayStatus:
    gateway_id: str
    name: str
    running: bool
    info: GatewayLaunchInfo | None
    exit_reason: str


class MCPGatewayManager:
    def __init__(
        self,
        store: GatewayProfileStore | None = None,
        log: LogCallback | None = None,
        permission_broker: "DesktopPermissionBroker | None" = None,
    ) -> None:
        self.store = store or GatewayProfileStore()
        self._log_callback = log or (lambda _message: None)
        self._permission_broker = permission_broker
        self._lock = threading.RLock()
        self._launchers: dict[str, MCPGatewayLauncher] = {}

    def _gateway_log(self, gateway: MCPGatewayProfile) -> LogCallback:
        def emit(message: str) -> None:
            self._log_callback(f"[Gateway:{gateway.name}] {message}")

        return emit

    def _launcher_for(self, gateway: MCPGatewayProfile) -> MCPGatewayLauncher:
        launcher = self._launchers.get(gateway.gateway_id)
        if launcher is None:
            launcher = MCPGatewayLauncher(
                self._gateway_log(gateway),
                permission_broker=self._permission_broker,
            )
            self._launchers[gateway.gateway_id] = launcher
        return launcher

    def profiles(self) -> list[MCPGatewayProfile]:
        return self.store.list()

    def start(self, gateway_id: str) -> GatewayLaunchInfo:
        with self._lock:
            gateway = self.store.get(gateway_id)
            if gateway is None:
                raise KeyError(f"找不到 Local MCP Gateway: {gateway_id}")
            launcher = self._launcher_for(gateway)
            if launcher.is_running:
                raise RuntimeError(f"Local MCP Gateway 已经在运行: {gateway.name}")
            return launcher.start(gateway.to_launch_config())

    def start_config(
        self,
        gateway_id: str,
        config: GatewayLaunchConfig,
    ) -> GatewayLaunchInfo:
        """Start a saved Gateway identity with runtime-only secret overrides."""

        with self._lock:
            gateway = self.store.get(gateway_id)
            if gateway is None:
                raise KeyError(f"找不到 Local MCP Gateway: {gateway_id}")
            launcher = self._launcher_for(gateway)
            if launcher.is_running:
                raise RuntimeError(f"Local MCP Gateway 已经在运行: {gateway.name}")
            validated = config.validated()
            saved_ids = [member.server_id for member in gateway.members]
            runtime_ids = [profile.server_id for profile in validated.profiles]
            if runtime_ids != saved_ids:
                raise ValueError("Gateway runtime Profile 身份与已保存配置不一致。")
            if validated.host != gateway.host or validated.port != gateway.port:
                raise ValueError("Gateway runtime 地址与已保存配置不一致。")
            return launcher.start(validated)

    def stop(self, gateway_id: str) -> None:
        with self._lock:
            launcher = self._launchers.get(gateway_id)
            if launcher is not None:
                launcher.stop()

    def stop_all(self) -> None:
        with self._lock:
            for launcher in tuple(self._launchers.values()):
                launcher.stop()

    def is_running(self, gateway_id: str) -> bool:
        with self._lock:
            launcher = self._launchers.get(gateway_id)
            return bool(launcher and launcher.is_running)

    def status(self, gateway_id: str) -> ManagedGatewayStatus:
        with self._lock:
            gateway = self.store.get(gateway_id)
            if gateway is None:
                raise KeyError(f"找不到 Local MCP Gateway: {gateway_id}")
            launcher = self._launchers.get(gateway_id)
            return ManagedGatewayStatus(
                gateway_id=gateway.gateway_id,
                name=gateway.name,
                running=bool(launcher and launcher.is_running),
                info=launcher.info if launcher else None,
                exit_reason=launcher.exit_reason if launcher else "",
            )

    def statuses(self) -> list[ManagedGatewayStatus]:
        return [self.status(gateway.gateway_id) for gateway in self.store.list()]

    def delete_profile(self, gateway_id: str) -> bool:
        with self._lock:
            launcher = self._launchers.get(gateway_id)
            if launcher and launcher.is_running:
                raise RuntimeError("请先停止 Local MCP Gateway，再删除配置。")
            deleted = self.store.delete(gateway_id)
            if deleted:
                self._launchers.pop(gateway_id, None)
            return deleted


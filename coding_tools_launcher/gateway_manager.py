from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .gateway_launcher import (
    GatewayDiagnosticReport,
    GatewayLaunchConfig,
    GatewayLaunchInfo,
    MCPGatewayLauncher,
)
from .gateway_profiles import GatewayProfileStore, MCPGatewayProfile
from .oauth_client_store import CIMDClientStore, OAuthClientStore, OAuthClientSummary
from .process_utils import LogCallback

if TYPE_CHECKING:
    from .permission_broker import DesktopPermissionBroker


@dataclass(frozen=True, slots=True)
class ManagedGatewayStatus:
    gateway_id: str
    name: str
    running: bool
    info: GatewayLaunchInfo | None
    diagnostic: GatewayDiagnosticReport | None
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
            if validated.mode != gateway.mode:
                raise ValueError("Service runtime mode 与已保存配置不一致。")
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
                diagnostic=launcher.last_diagnostic if launcher else None,
                exit_reason=launcher.exit_reason if launcher else "",
            )

    def statuses(self) -> list[ManagedGatewayStatus]:
        return [self.status(gateway.gateway_id) for gateway in self.store.list()]

    def _member_is_running(
        self,
        gateway: MCPGatewayProfile,
        server_id: str,
    ) -> bool:
        if not self.is_running(gateway.gateway_id):
            return False
        member = next(
            (item for item in gateway.members if item.server_id == server_id.strip()),
            None,
        )
        if member is None:
            return False
        return gateway.mode == "multi" or member.instance_path == ""

    def diagnose(self, gateway_id: str) -> GatewayDiagnosticReport:
        with self._lock:
            gateway = self.store.get(gateway_id)
            if gateway is None:
                raise KeyError(f"找不到 Local MCP Gateway: {gateway_id}")
            launcher = self._launchers.get(gateway_id)
            if launcher is None or not launcher.is_running:
                raise RuntimeError("请先启动 Local MCP Gateway，再执行公网自检。")
        return launcher.diagnose()

    def oauth_clients(
        self,
        gateway_id: str,
        server_id: str,
    ) -> list[OAuthClientSummary]:
        with self._lock:
            gateway = self.store.get(gateway_id)
            if gateway is None:
                raise KeyError(f"找不到 Local MCP Gateway: {gateway_id}")
            member = next(
                (item for item in gateway.members if item.server_id == server_id.strip()),
                None,
            )
            if member is None:
                raise KeyError(f"找不到 Gateway Profile: {server_id}")
            if member.lifecycle == "ephemeral":
                launcher = self._launchers.get(gateway.gateway_id)
                if not launcher or not launcher.is_running:
                    return []
                registry_file = launcher.oauth_registry_file(member.server_id)
                if registry_file is None:
                    return []
                clients = OAuthClientStore(member.server_id, path=registry_file).list()
                clients.extend(
                    CIMDClientStore(
                        member.server_id,
                        path=registry_file.with_name("cimd-clients.json"),
                    ).list()
                )
                return sorted(clients, key=lambda item: (item.issued_at, item.client_id))
            clients = OAuthClientStore(member.server_id).list()
            clients.extend(CIMDClientStore(member.server_id).list())
            return sorted(clients, key=lambda item: (item.issued_at, item.client_id))

    def remove_oauth_client(
        self,
        gateway_id: str,
        server_id: str,
        client_id: str,
    ) -> bool:
        with self._lock:
            gateway = self.store.get(gateway_id)
            if gateway is None:
                raise KeyError(f"找不到 Local MCP Gateway: {gateway_id}")
            member = next(
                (item for item in gateway.members if item.server_id == server_id.strip()),
                None,
            )
            if member is None:
                raise KeyError(f"找不到 Gateway Profile: {server_id}")
            if member.lifecycle == "ephemeral":
                raise RuntimeError("临时 Gateway Profile 的 OAuth Client 随 Session 自动销毁。")
            if self._member_is_running(gateway, member.server_id):
                raise RuntimeError("请先停止当前 Workspace Runtime，再撤销 OAuth Client。")
            return OAuthClientStore(member.server_id).remove(client_id)

    def clear_oauth_clients(self, gateway_id: str, server_id: str) -> int:
        with self._lock:
            gateway = self.store.get(gateway_id)
            if gateway is None:
                raise KeyError(f"找不到 Local MCP Gateway: {gateway_id}")
            member = next(
                (item for item in gateway.members if item.server_id == server_id.strip()),
                None,
            )
            if member is None:
                raise KeyError(f"找不到 Gateway Profile: {server_id}")
            if member.lifecycle == "ephemeral":
                return 0
            if self._member_is_running(gateway, member.server_id):
                raise RuntimeError("请先停止当前 Workspace Runtime，再撤销 OAuth Client。")
            return OAuthClientStore(member.server_id).clear()

    def delete_profile(self, gateway_id: str) -> bool:
        with self._lock:
            launcher = self._launchers.get(gateway_id)
            if launcher and launcher.is_running:
                raise RuntimeError("请先停止 Local MCP Gateway，再删除配置。")
            deleted = self.store.delete(gateway_id)
            if deleted:
                self._launchers.pop(gateway_id, None)
            return deleted


from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config import LaunchConfig, LaunchInfo
from .launcher import MCPLauncher
from .oauth_client_store import OAuthClientStore, OAuthClientSummary
from .oauth_persistence import (
    bound_server_oauth_issuer,
    delete_issuer_oauth_storage,
    delete_server_oauth_storage,
)
from .process_utils import LogCallback
from .server_profiles import MCPServerProfile, ServerProfileStore

if TYPE_CHECKING:
    from .permission_broker import DesktopPermissionBroker


@dataclass(frozen=True, slots=True)
class ManagedServerStatus:
    server_id: str
    name: str
    running: bool
    info: LaunchInfo | None
    exit_reason: str


class MCPServerManager:
    """Own and coordinate multiple independent MCP Server runtimes."""

    def __init__(
        self,
        store: ServerProfileStore | None = None,
        log: LogCallback | None = None,
        permission_broker: "DesktopPermissionBroker | None" = None,
    ) -> None:
        self.store = store or ServerProfileStore()
        self._log_callback = log or (lambda _message: None)
        self._lock = threading.RLock()
        self._launchers: dict[str, MCPLauncher] = {}
        self._permission_broker = permission_broker

    def _profile_log(self, profile: MCPServerProfile) -> LogCallback:
        def emit(message: str) -> None:
            self._log_callback(f"[{profile.name}] {message}")

        return emit

    def _launcher_for(self, profile: MCPServerProfile) -> MCPLauncher:
        launcher = self._launchers.get(profile.server_id)
        if launcher is None:
            log = self._profile_log(profile)
            launcher = (
                MCPLauncher(log, permission_broker=self._permission_broker)
                if self._permission_broker is not None
                else MCPLauncher(log)
            )
            self._launchers[profile.server_id] = launcher
        return launcher

    def profiles(self) -> list[MCPServerProfile]:
        return self.store.list()

    def start(self, server_id: str) -> LaunchInfo:
        with self._lock:
            profile = self.store.get(server_id)
            if profile is None:
                raise KeyError(f"找不到 MCP Server: {server_id}")
            launcher = self._launcher_for(profile)
            if launcher.is_running:
                raise RuntimeError(f"MCP Server 已经在运行: {profile.name}")
            return launcher.start(profile.to_launch_config())

    def start_config(self, server_id: str, config: LaunchConfig) -> LaunchInfo:
        """Start a saved Server identity with runtime-only secret overrides."""

        with self._lock:
            profile = self.store.get(server_id)
            if profile is None:
                raise KeyError(f"找不到 MCP Server: {server_id}")
            launcher = self._launcher_for(profile)
            if launcher.is_running:
                raise RuntimeError(f"MCP Server 已经在运行: {profile.name}")
            validated = config.validated()
            if validated.server_id != profile.server_id:
                raise ValueError("LaunchConfig server_id 与 Server Profile 不一致。")
            return launcher.start(validated)

    def stop(self, server_id: str) -> None:
        with self._lock:
            launcher = self._launchers.get(server_id)
            if launcher is not None:
                launcher.stop()

    def stop_all(self) -> None:
        with self._lock:
            for launcher in tuple(self._launchers.values()):
                launcher.stop()

    def is_running(self, server_id: str) -> bool:
        with self._lock:
            launcher = self._launchers.get(server_id)
            return bool(launcher and launcher.is_running)

    def status(self, server_id: str) -> ManagedServerStatus:
        with self._lock:
            profile = self.store.get(server_id)
            if profile is None:
                raise KeyError(f"找不到 MCP Server: {server_id}")
            launcher = self._launchers.get(server_id)
            return ManagedServerStatus(
                server_id=profile.server_id,
                name=profile.name,
                running=bool(launcher and launcher.is_running),
                info=launcher.info if launcher else None,
                exit_reason=launcher.exit_reason if launcher else "",
            )

    def statuses(self) -> list[ManagedServerStatus]:
        return [self.status(profile.server_id) for profile in self.store.list()]

    def delete_profile(self, server_id: str) -> bool:
        with self._lock:
            launcher = self._launchers.get(server_id)
            if launcher and launcher.is_running:
                raise RuntimeError("请先停止 MCP Server，再删除配置。")
            issuer = bound_server_oauth_issuer(server_id)
            deleted = self.store.delete(server_id)
            if deleted:
                self._launchers.pop(server_id, None)
                delete_server_oauth_storage(server_id)
                if issuer:
                    still_referenced = any(
                        bound_server_oauth_issuer(profile.server_id) == issuer
                        for profile in self.store.list()
                    )
                    if not still_referenced:
                        delete_issuer_oauth_storage(issuer)
            return deleted

    def oauth_clients(self, server_id: str) -> list[OAuthClientSummary]:
        profile = self.store.get(server_id)
        if profile is None:
            raise KeyError(f"找不到 MCP Server: {server_id}")
        if profile.lifecycle == "ephemeral":
            launcher = self._launchers.get(server_id)
            if not launcher or not launcher.is_running:
                return []
            registry_file = launcher.oauth_registry_file
            if registry_file is None:
                return []
            return OAuthClientStore(profile.server_id, path=registry_file).list()
        return OAuthClientStore(profile.server_id).list()

    def remove_oauth_client(self, server_id: str, client_id: str) -> bool:
        with self._lock:
            profile = self.store.get(server_id)
            if profile is None:
                raise KeyError(f"找不到 MCP Server: {server_id}")
            if profile.lifecycle == "ephemeral":
                raise RuntimeError("临时 Server 的 OAuth Client 随 Session 自动销毁。")
            if self.is_running(server_id):
                raise RuntimeError("请先停止 MCP Server，再撤销 OAuth Client。")
            return OAuthClientStore(profile.server_id).remove(client_id)

    def clear_oauth_clients(self, server_id: str) -> int:
        with self._lock:
            profile = self.store.get(server_id)
            if profile is None:
                raise KeyError(f"找不到 MCP Server: {server_id}")
            if profile.lifecycle == "ephemeral":
                return 0
            if self.is_running(server_id):
                raise RuntimeError("请先停止 MCP Server，再撤销 OAuth Client。")
            return OAuthClientStore(profile.server_id).clear()


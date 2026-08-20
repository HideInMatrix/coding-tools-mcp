from __future__ import annotations

import threading
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import LaunchConfig, NetworkConfig
from .executables import resolve_executable
from .gateway_manager import MCPGatewayManager
from .gateway_profiles import (
    GatewayProfileStore,
    MCPGatewayMember,
    MCPGatewayProfile,
)
from .oauth_persistence import (
    bound_server_oauth_issuer,
    delete_issuer_oauth_storage,
    delete_server_oauth_storage,
)
from .permission_broker import DesktopPermissionBroker
from .server_manager import MCPServerManager
from .server_profiles import MCPServerProfile, ServerProfileStore, default_lifecycle
from .self_update import UpdateManager
from .updates import (
    DEFAULT_GITHUB_DOWNLOAD_PROXY,
    fetch_latest_release,
    normalize_download_proxy_prefix,
)
from .user_settings import load_settings, save_settings, settings_dir
from .version import current_version
from .workbench_manager import DesktopWorkbenchManager


SENSITIVE_NETWORK_OPTIONS = {"tunnel_token", "authtoken"}
UPDATE_DOWNLOAD_PROXY_SETTING = "update_download_proxy_prefix"


class DesktopAPI:
    """Narrow JS ↔ Python bridge used by the pywebview frontend.

    The frontend never receives direct access to Manager/Store instances. All
    mutations go through this facade so validation, secret persistence rules
    and lifecycle constraints stay on the Python side.
    """

    def __init__(self, *, app_version: str | None = None) -> None:
        # Resolve once during process startup. The native window title, the
        # frontend and update checks must all report the exact same build.
        self._app_version = app_version or current_version()
        self.store = ServerProfileStore()
        self.gateway_store = GatewayProfileStore()
        self.permission_broker = DesktopPermissionBroker()
        self._log_lock = threading.RLock()
        self._log_cursor = 0
        self._logs: deque[dict[str, object]] = deque(maxlen=2000)
        self.manager = MCPServerManager(
            store=self.store,
            log=self._append_log,
            permission_broker=self.permission_broker,
        )
        self.gateway_manager = MCPGatewayManager(
            store=self.gateway_store,
            log=self._append_log,
            permission_broker=self.permission_broker,
        )
        self.workbench_manager = DesktopWorkbenchManager(
            server_store=self.store,
            gateway_store=self.gateway_store,
            server_manager=self.manager,
            gateway_manager=self.gateway_manager,
            global_root=settings_dir() / "workbench",
        )
        self.update_manager = UpdateManager(log=self._append_log)
        self._latest_release = None
        self._window: Any | None = None
        self._permission_attention_id = ""
        self._workflow_approval_attention_id = ""

    def _bind_window(self, window: Any) -> None:
        self._window = window

    def _close(self) -> None:
        self.manager.stop_all()
        self.gateway_manager.stop_all()
        self.update_manager.cleanup()
        self.permission_broker.cleanup()

    def list_permission_requests(self) -> list[dict[str, object]]:
        requests = self.permission_broker.pending()
        names = {profile.server_id: profile.name for profile in self.store.list()}
        for gateway in self.gateway_store.list():
            names.update(
                {member.server_id: member.name for member in gateway.members}
            )
        payload = [
            {
                **item,
                "server_name": names.get(str(item.get("server_id") or ""), "MCP Server"),
            }
            for item in requests
        ]
        request_id = str(payload[0].get("request_id") or "") if payload else ""
        if request_id and request_id != self._permission_attention_id:
            self._permission_attention_id = request_id
            window = self._window
            if window is not None:
                try:
                    window.show()
                    window.restore()
                except Exception:
                    pass
        elif not request_id:
            self._permission_attention_id = ""
        return payload

    def respond_permission_request(self, request_id: str, decision: str | bool) -> bool:
        return self.permission_broker.respond(str(request_id), decision)

    def list_workflow_approvals(self) -> list[dict[str, object]]:
        requests = self.permission_broker.pending_workflow_approvals()
        names = {profile.server_id: profile.name for profile in self.store.list()}
        for gateway in self.gateway_store.list():
            names.update(
                {member.server_id: member.name for member in gateway.members}
            )
        payload = [
            {
                **item,
                "server_name": names.get(
                    str(item.get("server_id") or ""),
                    "MCP Server",
                ),
            }
            for item in requests
        ]
        request_id = str(payload[0].get("request_id") or "") if payload else ""
        if request_id and request_id != self._workflow_approval_attention_id:
            self._workflow_approval_attention_id = request_id
            window = self._window
            if window is not None:
                try:
                    window.show()
                    window.restore()
                except Exception:
                    pass
        elif not request_id:
            self._workflow_approval_attention_id = ""
        return payload

    def respond_workflow_approval(self, request_id: str, approved: bool) -> bool:
        return self.permission_broker.respond_workflow_approval(
            str(request_id),
            bool(approved),
        )

    def list_workbench_targets(self) -> list[dict[str, object]]:
        return [item.to_dict() for item in self.workbench_manager.targets()]

    def get_workbench_catalog(self, target_id: str) -> dict[str, object]:
        return self.workbench_manager.catalog(str(target_id))

    def get_workbench_capability_catalog(self) -> dict[str, object]:
        return self.workbench_manager.capability_catalog()

    def get_workbench_mcp_connection(self, connection_id: str) -> dict[str, object]:
        return self.workbench_manager.get_mcp_connection(str(connection_id))

    def validate_workbench_mcp_connection(
        self,
        connection: dict[str, Any],
    ) -> dict[str, object]:
        return self.workbench_manager.validate_mcp_connection(connection)

    def save_workbench_mcp_connection(
        self,
        connection: dict[str, Any],
        expected_version: int,
    ) -> dict[str, object]:
        return self.workbench_manager.save_mcp_connection(
            connection,
            expected_version=int(expected_version),
        )

    def delete_workbench_mcp_connection(self, connection_id: str) -> bool:
        return self.workbench_manager.delete_mcp_connection(str(connection_id))

    def test_workbench_mcp_connection(
        self,
        connection_id: str,
        timeout_seconds: int = 8,
    ) -> dict[str, object]:
        return self.workbench_manager.test_mcp_connection(
            str(connection_id),
            timeout_seconds=int(timeout_seconds),
        )

    def discover_workbench_mcp_connection_tools(
        self,
        connection_id: str,
        timeout_seconds: int = 8,
    ) -> dict[str, object]:
        return self.workbench_manager.discover_mcp_connection_tools(
            str(connection_id),
            timeout_seconds=int(timeout_seconds),
        )

    def get_workbench_skill(
        self,
        skill_id: str,
    ) -> dict[str, object]:
        return self.workbench_manager.get_skill(str(skill_id))

    def validate_workbench_skill(
        self,
        skill: dict[str, Any],
    ) -> dict[str, object]:
        return self.workbench_manager.validate_skill(skill)

    def save_workbench_skill(
        self,
        skill: dict[str, Any],
        expected_version: int,
    ) -> dict[str, object]:
        return self.workbench_manager.save_skill(
            skill,
            expected_version=int(expected_version),
        )

    def delete_workbench_skill(self, skill_id: str) -> bool:
        return self.workbench_manager.delete_skill(str(skill_id))

    def get_workbench_workflow(
        self,
        target_id: str,
        workflow_id: str,
    ) -> dict[str, object]:
        return self.workbench_manager.get_workflow(
            str(target_id),
            str(workflow_id),
        )

    def validate_workbench_workflow(
        self,
        target_id: str,
        workflow: dict[str, Any],
    ) -> dict[str, object]:
        return self.workbench_manager.validate_workflow(str(target_id), workflow)

    def save_workbench_workflow(
        self,
        target_id: str,
        workflow: dict[str, Any],
        expected_version: int,
    ) -> dict[str, object]:
        return self.workbench_manager.save_workflow(
            str(target_id),
            workflow,
            expected_version=int(expected_version),
        )

    def delete_workbench_workflow(self, target_id: str, workflow_id: str) -> bool:
        return self.workbench_manager.delete_workflow(
            str(target_id),
            str(workflow_id),
        )

    def list_workbench_runs(self, target_id: str) -> list[dict[str, object]]:
        return self.workbench_manager.runs(str(target_id))

    def _append_log(self, message: str) -> None:
        with self._log_lock:
            self._log_cursor += 1
            self._logs.append(
                {
                    "id": self._log_cursor,
                    "time": int(time.time()),
                    "message": str(message),
                }
            )

    def _network_from_payload(self, raw: object) -> NetworkConfig:
        value = raw if isinstance(raw, dict) else {}
        raw_options = value.get("options")
        options = raw_options if isinstance(raw_options, dict) else {}
        return NetworkConfig(
            provider=str(value.get("provider") or "cloudflare"),
            public_url=str(value.get("public_url") or ""),
            options={str(key): str(option) for key, option in options.items()},
        ).validated()

    def _persistable_network(self, network: NetworkConfig, remember: bool) -> NetworkConfig:
        options = dict(network.options)
        if not remember:
            for key in SENSITIVE_NETWORK_OPTIONS:
                options.pop(key, None)
        return NetworkConfig(
            provider=network.provider,
            public_url=network.public_url,
            options=options,
        ).validated()

    def _profile_payload(self, profile: MCPServerProfile) -> dict[str, object]:
        status = self.manager.status(profile.server_id)
        info = status.info
        try:
            oauth_client_count = len(self.manager.oauth_clients(profile.server_id))
        except Exception:
            oauth_client_count = 0
        return {
            "server_id": profile.server_id,
            "name": profile.name,
            "workspace": str(profile.workspace),
            "oauth_password": profile.oauth_password,
            "has_saved_password": bool(profile.oauth_password),
            "host": profile.host,
            "port": profile.port,
            "lifecycle": profile.lifecycle,
            "permission_mode": profile.permission_mode,
            "allow_network": profile.allow_network,
            "enable_view_image": profile.enable_view_image,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
            "network": {
                "provider": profile.network.provider,
                "public_url": profile.network.public_url,
                "options": dict(profile.network.options),
            },
            "running": status.running,
            "public_mcp_url": info.public_mcp_url if info else "",
            "url_mode": info.url_mode if info else "",
            "exit_reason": status.exit_reason,
            "oauth_client_count": oauth_client_count,
        }

    @staticmethod
    def _public_hostname(network: NetworkConfig) -> str:
        if not network.public_url:
            return ""
        return (urlsplit(network.public_url).hostname or "").lower()

    def _next_available_port(self, start: int = 8234) -> int:
        used = {profile.port for profile in self.store.list()}
        used.update(gateway.port for gateway in self.gateway_store.list())
        for port in range(max(1, int(start)), 65536):
            if port not in used:
                return port
        raise RuntimeError("没有可用的 TCP 端口可分配。")

    def _assert_direct_resources_available(
        self,
        *,
        network: NetworkConfig,
        host: str,
        port: int,
    ) -> None:
        hostname = self._public_hostname(network)
        for gateway in self.gateway_store.list():
            if gateway.host == host and gateway.port == port:
                raise ValueError(
                    f"该地址已被 Local MCP Gateway 使用: {host}:{port}"
                )
            if hostname and hostname == self._public_hostname(gateway.network):
                raise ValueError(
                    "该 Public Hostname 已被 Local MCP Gateway 使用；"
                    "直连 Server 必须使用独立 hostname。"
                )

    def _assert_gateway_resources_available(
        self,
        *,
        network: NetworkConfig,
        host: str,
        port: int,
        ignore_server_id: str = "",
    ) -> None:
        hostname = self._public_hostname(network)
        for profile in self.store.list():
            if ignore_server_id and profile.server_id == ignore_server_id:
                continue
            if profile.host == host and profile.port == port:
                raise ValueError(
                    f"该地址已被直连 MCP Server 使用: {host}:{port}"
                )
            if hostname and hostname == self._public_hostname(profile.network):
                raise ValueError(
                    "该 Public Hostname 已被直连 MCP Server 使用；"
                    "Gateway 必须使用独立 hostname。"
                )

    def _gateway_payload(self, gateway: MCPGatewayProfile) -> dict[str, object]:
        status = self.gateway_manager.status(gateway.gateway_id)
        info = status.info
        members: list[dict[str, object]] = []
        for member in gateway.members:
            runtime_info = info.profile(member.server_id) if info else None
            try:
                oauth_client_count = len(
                    self.gateway_manager.oauth_clients(
                        gateway.gateway_id,
                        member.server_id,
                    )
                )
            except Exception:
                oauth_client_count = 0
            members.append(
                {
                    "server_id": member.server_id,
                    "name": member.name,
                    "workspace": str(member.workspace),
                    "oauth_password": member.oauth_password,
                    "has_saved_password": bool(member.oauth_password),
                    "instance_path": member.instance_path,
                    "permission_mode": member.permission_mode,
                    "lifecycle": member.lifecycle,
                    "allow_network": member.allow_network,
                    "enable_view_image": member.enable_view_image,
                    "public_mcp_url": (
                        runtime_info.public_mcp_url if runtime_info else ""
                    ),
                    "local_mcp_url": (
                        runtime_info.local_mcp_url if runtime_info else ""
                    ),
                    "oauth_issuer": (
                        runtime_info.oauth_issuer if runtime_info else ""
                    ),
                    "oauth_client_count": oauth_client_count,
                }
            )
        return {
            "gateway_id": gateway.gateway_id,
            "name": gateway.name,
            "mode": gateway.mode,
            "host": gateway.host,
            "port": gateway.port,
            "created_at": gateway.created_at,
            "updated_at": gateway.updated_at,
            "network": {
                "provider": gateway.network.provider,
                "public_url": gateway.network.public_url,
                "options": dict(gateway.network.options),
            },
            "members": members,
            "running": status.running,
            "public_base_url": info.public_base_url if info else "",
            "url_mode": info.url_mode if info else "",
            "exit_reason": status.exit_reason,
            "diagnostic": (
                self._gateway_diagnostic_payload(status.diagnostic)
                if status.diagnostic is not None
                else None
            ),
        }

    @staticmethod
    def _gateway_diagnostic_payload(report: Any) -> dict[str, object]:
        return {
            "ok": bool(report.ok),
            "public_base_url": str(report.public_base_url),
            "checked_at": int(report.checked_at),
            "profiles": [
                {
                    "server_id": profile.server_id,
                    "name": profile.name,
                    "instance_path": profile.instance_path,
                    "ok": profile.ok,
                    "checks": list(profile.checks),
                    "errors": list(profile.errors),
                }
                for profile in report.profiles
            ],
        }

    def _gateway_member_from_payload(
        self,
        raw: object,
        *,
        lifecycle: str,
        remember_secrets: bool,
        current: MCPGatewayMember | None = None,
    ) -> MCPGatewayMember:
        value = raw if isinstance(raw, dict) else {}
        server_id = str(value.get("server_id") or (current.server_id if current else ""))
        password = (
            str(value.get("oauth_password") or (current.oauth_password if current else ""))
            if remember_secrets
            else ""
        )
        if server_id:
            return MCPGatewayMember(
                server_id=server_id,
                name=str(value.get("name") or (current.name if current else "")),
                workspace=Path(
                    str(value.get("workspace") or (current.workspace if current else ""))
                ),
                oauth_password=password,
                instance_path=str(
                    value.get("instance_path")
                    or (current.instance_path if current else "")
                ),
                permission_mode=str(
                    value.get("permission_mode")
                    or (current.permission_mode if current else "safe")
                ),
                lifecycle=lifecycle,
                allow_network=bool(
                    value.get(
                        "allow_network",
                        current.allow_network if current else False,
                    )
                ),
                enable_view_image=bool(
                    value.get(
                        "enable_view_image",
                        current.enable_view_image if current else True,
                    )
                ),
            ).validated()
        return MCPGatewayMember.create(
            name=str(value.get("name") or ""),
            workspace=Path(str(value.get("workspace") or "")),
            oauth_password=password,
            instance_path=str(value.get("instance_path") or ""),
            permission_mode=str(value.get("permission_mode") or "safe"),
            lifecycle=lifecycle,
            allow_network=bool(value.get("allow_network", False)),
            enable_view_image=bool(value.get("enable_view_image", True)),
        )

    def _release_payload(self, info: Any) -> dict[str, object]:
        return {
            "current_version": info.current_version,
            "latest_version": info.latest_version,
            "tag_name": info.tag_name,
            "release_url": info.release_url,
            "asset_name": info.asset_name,
            "download_url": info.download_url,
            "update_asset_name": info.update_asset_name,
            "update_download_url": info.update_download_url,
            "checksum_url": info.checksum_url,
            "update_available": info.update_available,
        }

    def _selected_server_id(self) -> str:
        return str(load_settings().get("selected_server_id") or "")

    def _save_selected_server_id(self, server_id: str) -> None:
        settings = load_settings()
        settings["selected_server_id"] = server_id
        save_settings(settings)

    def _update_download_proxy_prefix(self) -> str:
        settings = load_settings()
        raw = settings.get(
            UPDATE_DOWNLOAD_PROXY_SETTING,
            DEFAULT_GITHUB_DOWNLOAD_PROXY,
        )
        try:
            return normalize_download_proxy_prefix(raw)
        except ValueError:
            return DEFAULT_GITHUB_DOWNLOAD_PROXY

    def save_update_download_proxy(self, prefix: str) -> str:
        normalized = normalize_download_proxy_prefix(prefix)
        settings = load_settings()
        settings[UPDATE_DOWNLOAD_PROXY_SETTING] = normalized
        save_settings(settings)
        # ReleaseInfo contains the effective asset URLs, so force a refresh.
        self._latest_release = None
        return normalized

    def bootstrap(self) -> dict[str, object]:
        profiles = self.store.list()
        gateways = self.gateway_store.list()
        selected = self._selected_server_id()
        ids = {profile.server_id for profile in profiles}
        if selected not in ids:
            selected = profiles[0].server_id if profiles else ""
        return {
            "app_name": "MicroMatrix Workbench",
            "version": self._app_version,
            "update_download_proxy_prefix": self._update_download_proxy_prefix(),
            "selected_server_id": selected,
            "next_default_port": self._next_available_port(),
            "servers": [self._profile_payload(profile) for profile in profiles],
            "gateways": [self._gateway_payload(gateway) for gateway in gateways],
            "network_providers": [
                {"key": "cloudflare", "label": "Cloudflare Tunnel"},
                {"key": "frp", "label": "FRP"},
                {"key": "ngrok", "label": "ngrok"},
                {"key": "tailscale", "label": "Tailscale Funnel"},
                {"key": "external", "label": "自定义公网 URL"},
            ],
        }

    def get_app_version(self) -> str:
        """Return version metadata without waiting for the full bootstrap."""
        return self._app_version

    def get_selected_server_id(self) -> str:
        """Return the persisted selected Server without requiring bootstrap."""

        profiles = self.store.list()
        selected = self._selected_server_id()
        ids = {profile.server_id for profile in profiles}
        if selected in ids:
            return selected
        return profiles[0].server_id if profiles else ""

    def get_update_download_proxy(self) -> str:
        """Return the effective GitHub download proxy setting."""

        return self._update_download_proxy_prefix()

    def list_servers(self) -> list[dict[str, object]]:
        return [self._profile_payload(profile) for profile in self.store.list()]

    def get_next_port(self) -> int:
        return self._next_available_port()

    def select_server(self, server_id: str) -> bool:
        if self.store.get(server_id) is None:
            return False
        self._save_selected_server_id(server_id)
        return True

    def create_server(self, payload: dict[str, object]) -> dict[str, object]:
        network = self._network_from_payload(payload.get("network"))
        remember = bool(payload.get("remember_secrets", True))
        host = str(payload.get("host") or "127.0.0.1")
        port = int(payload.get("port") or self._next_available_port())
        self._assert_direct_resources_available(
            network=network,
            host=host,
            port=port,
        )
        profile = self.store.create(
            name=str(payload.get("name") or ""),
            workspace=Path(str(payload.get("workspace") or "")),
            oauth_password=str(payload.get("oauth_password") or "") if remember else "",
            network=self._persistable_network(network, remember),
            host=host,
            port=port,
            lifecycle=default_lifecycle(network),
            permission_mode=str(payload.get("permission_mode") or "safe"),
            allow_network=bool(payload.get("allow_network", False)),
            enable_view_image=bool(payload.get("enable_view_image", True)),
        )
        self._save_selected_server_id(profile.server_id)
        return self._profile_payload(profile)

    def update_server(self, server_id: str, payload: dict[str, object]) -> dict[str, object]:
        current = self.store.get(server_id)
        if current is None:
            raise KeyError(f"找不到 MCP Server: {server_id}")
        if self.manager.is_running(server_id):
            raise RuntimeError("请先停止当前 MCP Server，再修改配置。")

        network = self._network_from_payload(payload.get("network"))
        remember = bool(payload.get("remember_secrets", True))
        host = str(payload.get("host") or current.host)
        port = int(payload.get("port") or current.port)
        self._assert_direct_resources_available(
            network=network,
            host=host,
            port=port,
        )
        profile = self.store.save(
            MCPServerProfile(
                server_id=current.server_id,
                name=str(payload.get("name") or current.name),
                workspace=Path(str(payload.get("workspace") or current.workspace)),
                oauth_password=(
                    str(payload.get("oauth_password") or "") if remember else ""
                ),
                network=self._persistable_network(network, remember),
                host=host,
                port=port,
                lifecycle=default_lifecycle(network),
                permission_mode=str(
                    payload.get("permission_mode") or current.permission_mode
                ),
                allow_network=bool(
                    payload.get("allow_network", current.allow_network)
                ),
                enable_view_image=bool(
                    payload.get("enable_view_image", current.enable_view_image)
                ),
                created_at=current.created_at,
                updated_at=current.updated_at,
            )
        )
        return self._profile_payload(profile)

    def delete_server(self, server_id: str) -> bool:
        deleted = self.manager.delete_profile(server_id)
        if deleted:
            self.permission_broker.clear_server(server_id)
        if deleted and self._selected_server_id() == server_id:
            profiles = self.store.list()
            self._save_selected_server_id(profiles[0].server_id if profiles else "")
        return deleted

    def start_server(
        self,
        server_id: str,
        runtime_payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        profile = self.store.get(server_id)
        if profile is None:
            raise KeyError(f"找不到 MCP Server: {server_id}")

        if runtime_payload:
            raw_network = runtime_payload.get("network")
            network_payload = raw_network if isinstance(raw_network, dict) else {}
            runtime_network = self._network_from_payload(network_payload)
            if (
                runtime_network.provider != profile.network.provider
                or runtime_network.public_url != profile.network.public_url
            ):
                raise ValueError("运行配置与已保存 Server Profile 不一致，请先保存配置。")
            merged_options = dict(profile.network.options)
            for key, value in runtime_network.options.items():
                if value:
                    merged_options[key] = value
            network = NetworkConfig(
                provider=profile.network.provider,
                public_url=profile.network.public_url,
                options=merged_options,
            ).validated()
            config = LaunchConfig(
                workspace=profile.workspace,
                oauth_password=str(
                    runtime_payload.get("oauth_password") or profile.oauth_password
                ),
                network=network,
                host=profile.host,
                port=profile.port,
                server_id=profile.server_id,
                lifecycle=profile.lifecycle,
                permission_mode=profile.permission_mode,
                allow_network=profile.allow_network,
                enable_view_image=profile.enable_view_image,
            ).validated()
            self.manager.start_config(server_id, config)
        else:
            self.manager.start(server_id)
        return self._profile_payload(self.store.get(server_id) or profile)

    def stop_server(self, server_id: str) -> dict[str, object]:
        self.manager.stop(server_id)
        self.permission_broker.clear_server(server_id)
        profile = self.store.get(server_id)
        if profile is None:
            raise KeyError(f"找不到 MCP Server: {server_id}")
        return self._profile_payload(profile)

    def list_gateways(self) -> list[dict[str, object]]:
        return [
            self._gateway_payload(gateway)
            for gateway in self.gateway_store.list()
        ]

    def create_gateway(self, payload: dict[str, object]) -> dict[str, object]:
        network = self._network_from_payload(payload.get("network"))
        remember = bool(payload.get("remember_secrets", True))
        host = str(payload.get("host") or "127.0.0.1")
        port = int(payload.get("port") or self._next_available_port())
        self._assert_gateway_resources_available(
            network=network,
            host=host,
            port=port,
        )
        raw_members = payload.get("members")
        if not isinstance(raw_members, list) or not raw_members:
            raise ValueError("Gateway 至少需要一个 Member。")
        lifecycle = default_lifecycle(network)
        members = tuple(
            self._gateway_member_from_payload(
                raw,
                lifecycle=lifecycle,
                remember_secrets=remember,
            )
            for raw in raw_members
        )
        gateway = self.gateway_store.create(
            name=str(payload.get("name") or ""),
            network=self._persistable_network(network, remember),
            members=members,
            mode=str(payload.get("mode") or "multi"),
            host=host,
            port=port,
        )
        return self._gateway_payload(gateway)

    def _updated_gateway_members(
        self,
        current: MCPGatewayProfile,
        raw_members: object,
        *,
        lifecycle: str,
        remember_secrets: bool,
    ) -> tuple[tuple[MCPGatewayMember, ...], tuple[MCPGatewayMember, ...]]:
        current_by_id = {member.server_id: member for member in current.members}
        if raw_members is None:
            raw_members = [member.to_dict() for member in current.members]
        if not isinstance(raw_members, list) or not raw_members:
            raise ValueError("Gateway 至少需要一个 Member。")
        members = tuple(
            self._gateway_member_from_payload(
                raw if isinstance(raw, dict) else {},
                lifecycle=lifecycle,
                remember_secrets=remember_secrets,
                current=current_by_id.get(
                    str(raw.get("server_id") or "")
                    if isinstance(raw, dict)
                    else ""
                ),
            )
            for raw in raw_members
        )
        member_ids = {member.server_id for member in members}
        removed = tuple(
            member for member in current.members if member.server_id not in member_ids
        )
        return members, removed

    def _cleanup_removed_gateway_members(
        self,
        removed_members: tuple[MCPGatewayMember, ...],
    ) -> None:
        issuers = {
            member.server_id: bound_server_oauth_issuer(member.server_id)
            for member in removed_members
        }
        for member in removed_members:
            self.permission_broker.clear_server(member.server_id)
            delete_server_oauth_storage(member.server_id)
            issuer = issuers.get(member.server_id)
            if issuer and not self._oauth_identity_still_referenced(issuer):
                delete_issuer_oauth_storage(issuer)

    def _promoted_gateway_members(
        self,
        current: MCPServerProfile,
        raw_members: object,
        *,
        lifecycle: str,
        remember_secrets: bool,
    ) -> tuple[MCPGatewayMember, ...]:
        if not isinstance(raw_members, list) or not raw_members:
            raise ValueError("服务至少需要一个 Workspace Profile。")
        members: list[MCPGatewayMember] = []
        root_seen = False
        for raw in raw_members:
            value = raw if isinstance(raw, dict) else {}
            instance_path = str(value.get("instance_path") or "")
            if instance_path.strip().strip("/"):
                members.append(
                    self._gateway_member_from_payload(
                        value,
                        lifecycle=lifecycle,
                        remember_secrets=remember_secrets,
                    )
                )
                continue
            if root_seen:
                raise ValueError("一个服务只能有一个根 Workspace Profile。")
            root_seen = True
            members.append(
                MCPGatewayMember(
                    server_id=current.server_id,
                    name=str(value.get("name") or current.name),
                    workspace=Path(str(value.get("workspace") or current.workspace)),
                    oauth_password=(
                        str(value.get("oauth_password") or current.oauth_password)
                        if remember_secrets
                        else ""
                    ),
                    instance_path="",
                    permission_mode=str(
                        value.get("permission_mode") or current.permission_mode
                    ),
                    lifecycle=lifecycle,
                    allow_network=bool(value.get("allow_network", False)),
                    enable_view_image=bool(value.get("enable_view_image", True)),
                ).validated()
            )
        if not root_seen:
            raise ValueError(
                "从单 Workspace 升级时必须保留原来的根 Workspace Profile。"
            )
        if len(members) < 2:
            raise ValueError("没有新增子 Profile，无需切换到多 Workspace Runtime。")
        return tuple(members)

    def update_gateway(
        self,
        gateway_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        current = self.gateway_store.get(gateway_id)
        if current is None:
            raise KeyError(f"找不到 Local MCP Gateway: {gateway_id}")
        if self.gateway_manager.is_running(gateway_id):
            raise RuntimeError("请先停止 Local MCP Gateway，再修改配置。")

        network = self._network_from_payload(payload.get("network"))
        remember = bool(payload.get("remember_secrets", True))
        host = str(payload.get("host") or current.host)
        port = int(payload.get("port") or current.port)
        self._assert_gateway_resources_available(
            network=network,
            host=host,
            port=port,
        )
        lifecycle = default_lifecycle(network)
        members, removed_members = self._updated_gateway_members(
            current,
            payload.get("members"),
            lifecycle=lifecycle,
            remember_secrets=remember,
        )
        gateway = self.gateway_store.save(
            MCPGatewayProfile(
                gateway_id=current.gateway_id,
                name=str(payload.get("name") or current.name),
                network=self._persistable_network(network, remember),
                members=members,
                mode=str(payload.get("mode") or current.mode),
                host=host,
                port=port,
                created_at=current.created_at,
                updated_at=current.updated_at,
            )
        )
        self._cleanup_removed_gateway_members(removed_members)
        return self._gateway_payload(gateway)

    def promote_server_to_gateway(
        self,
        server_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Promote a Direct Server into one multi-profile Service.

        The original server_id becomes the root Profile identity, so `/mcp`,
        OAuth issuer storage, and Desktop Permission Broker identity remain
        stable while child Profiles are added.
        """

        current = self.store.get(server_id)
        if current is None:
            raise KeyError(f"找不到 MCP Server: {server_id}")
        if self.manager.is_running(server_id):
            raise RuntimeError("请先停止当前服务，再添加子 Profile。")

        network = self._network_from_payload(payload.get("network"))
        remember = bool(payload.get("remember_secrets", True))
        host = str(payload.get("host") or current.host)
        port = int(payload.get("port") or current.port)
        self._assert_gateway_resources_available(
            network=network,
            host=host,
            port=port,
            ignore_server_id=server_id,
        )
        lifecycle = default_lifecycle(network)
        members = self._promoted_gateway_members(
            current,
            payload.get("members"),
            lifecycle=lifecycle,
            remember_secrets=remember,
        )

        gateway = self.gateway_store.save(
            MCPGatewayProfile(
                gateway_id=current.server_id,
                name=str(payload.get("name") or current.name),
                network=self._persistable_network(network, remember),
                members=members,
                mode=str(payload.get("mode") or "multi"),
                host=host,
                port=port,
                created_at=current.created_at,
                updated_at=current.updated_at,
            )
        )
        # Only remove the legacy Direct record. The root Profile keeps the
        # original identity, so no OAuth/Broker cleanup is performed.
        if not self.store.delete(server_id):
            self.gateway_store.delete(gateway.gateway_id)
            raise RuntimeError("服务运行模型转换失败：旧 Direct Profile 未能移除。")
        return self._gateway_payload(gateway)

    def _oauth_identity_still_referenced(self, issuer: str) -> bool:
        for profile in self.store.list():
            if bound_server_oauth_issuer(profile.server_id) == issuer:
                return True
        for gateway in self.gateway_store.list():
            for member in gateway.members:
                if bound_server_oauth_issuer(member.server_id) == issuer:
                    return True
        return False

    def delete_gateway(self, gateway_id: str) -> bool:
        gateway = self.gateway_store.get(gateway_id)
        if gateway is None:
            return False
        issuers = {
            member.server_id: bound_server_oauth_issuer(member.server_id)
            for member in gateway.members
        }
        deleted = self.gateway_manager.delete_profile(gateway_id)
        if not deleted:
            return False
        for member in gateway.members:
            self.permission_broker.clear_server(member.server_id)
            delete_server_oauth_storage(member.server_id)
            issuer = issuers.get(member.server_id)
            if issuer and not self._oauth_identity_still_referenced(issuer):
                delete_issuer_oauth_storage(issuer)
        return True

    def start_gateway(
        self,
        gateway_id: str,
        runtime_payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        gateway = self.gateway_store.get(gateway_id)
        if gateway is None:
            raise KeyError(f"找不到 Local MCP Gateway: {gateway_id}")

        if runtime_payload:
            raw_network = runtime_payload.get("network")
            network_payload = raw_network if isinstance(raw_network, dict) else {}
            runtime_network = self._network_from_payload(network_payload)
            merged_options = dict(gateway.network.options)
            for key, value in runtime_network.options.items():
                if value:
                    merged_options[key] = value
            network = NetworkConfig(
                provider=gateway.network.provider,
                public_url=gateway.network.public_url,
                options=merged_options,
            ).validated()
            raw_members = runtime_payload.get("members")
            oauth_passwords: dict[str, str] = {}
            if isinstance(raw_members, list):
                for raw in raw_members:
                    if not isinstance(raw, dict):
                        continue
                    server_id = str(raw.get("server_id") or "")
                    if server_id:
                        oauth_passwords[server_id] = str(
                            raw.get("oauth_password") or ""
                        )
            config = gateway.runtime_launch_config(
                network=network,
                oauth_passwords=oauth_passwords,
            )
            self.gateway_manager.start_config(gateway_id, config)
        else:
            self.gateway_manager.start(gateway_id)
        return self._gateway_payload(self.gateway_store.get(gateway_id) or gateway)

    def stop_gateway(self, gateway_id: str) -> dict[str, object]:
        self.gateway_manager.stop(gateway_id)
        gateway = self.gateway_store.get(gateway_id)
        if gateway is None:
            raise KeyError(f"找不到 Local MCP Gateway: {gateway_id}")
        for member in gateway.members:
            self.permission_broker.clear_server(member.server_id)
        return self._gateway_payload(gateway)

    def test_gateway(self, gateway_id: str) -> dict[str, object]:
        report = self.gateway_manager.diagnose(gateway_id)
        return self._gateway_diagnostic_payload(report)

    def list_oauth_clients(self, server_id: str) -> list[dict[str, object]]:
        return [
            {
                "client_id": client.client_id,
                "client_name": client.client_name or "未命名客户端",
                "redirect_uris": list(client.redirect_uris),
                "token_endpoint_auth_method": client.token_endpoint_auth_method,
                "issued_at": client.issued_at,
                "client_type": client.client_type,
                "revocable": client.revocable,
            }
            for client in self.manager.oauth_clients(server_id)
        ]

    def list_gateway_oauth_clients(
        self,
        gateway_id: str,
        server_id: str,
    ) -> list[dict[str, object]]:
        return [
            {
                "client_id": client.client_id,
                "client_name": client.client_name or "未命名客户端",
                "redirect_uris": list(client.redirect_uris),
                "token_endpoint_auth_method": client.token_endpoint_auth_method,
                "issued_at": client.issued_at,
                "client_type": client.client_type,
                "revocable": client.revocable,
            }
            for client in self.gateway_manager.oauth_clients(gateway_id, server_id)
        ]

    def revoke_oauth_client(self, server_id: str, client_id: str) -> bool:
        return self.manager.remove_oauth_client(server_id, client_id)

    def revoke_all_oauth_clients(self, server_id: str) -> int:
        return self.manager.clear_oauth_clients(server_id)

    def revoke_gateway_oauth_client(
        self,
        gateway_id: str,
        server_id: str,
        client_id: str,
    ) -> bool:
        return self.gateway_manager.remove_oauth_client(
            gateway_id,
            server_id,
            client_id,
        )

    def revoke_all_gateway_oauth_clients(
        self,
        gateway_id: str,
        server_id: str,
    ) -> int:
        return self.gateway_manager.clear_oauth_clients(gateway_id, server_id)

    def get_logs(self, after: int = 0) -> dict[str, object]:
        with self._log_lock:
            entries = [entry for entry in self._logs if int(entry["id"]) > int(after)]
            return {"cursor": self._log_cursor, "entries": entries}

    def clear_logs(self) -> int:
        """Clear retained desktop runtime logs and return the current cursor.

        The cursor is intentionally not reset so a polling frontend can continue
        from the clear boundary without replaying older entries or colliding with
        IDs emitted immediately after the clear.
        """
        with self._log_lock:
            self._logs.clear()
            return self._log_cursor

    def detect_executable(self, product: str, configured: str = "") -> dict[str, object]:
        candidate = resolve_executable(product, configured=configured, auto_only=True)
        return {
            "path": str(candidate.path),
            "source": candidate.source,
            "version": candidate.version,
        }

    def choose_workspace(self, initial: str = "") -> str:
        if self._window is None:
            return ""
        import webview

        result = self._window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=initial or str(Path.home()),
        )
        return str(result[0]) if result else ""

    def choose_file(self, initial: str = "") -> str:
        if self._window is None:
            return ""
        import webview

        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=initial or str(Path.home()),
        )
        return str(result[0]) if result else ""

    def check_update(self) -> dict[str, object]:
        info = fetch_latest_release(
            self._app_version,
            download_proxy_prefix=self._update_download_proxy_prefix(),
        )
        self._latest_release = info
        return self._release_payload(info)

    def start_update(self) -> dict[str, object]:
        info = self._latest_release
        if info is None:
            info = fetch_latest_release(
                self._app_version,
                download_proxy_prefix=self._update_download_proxy_prefix(),
            )
            self._latest_release = info
        return self.update_manager.start(info).to_dict()

    def update_status(self) -> dict[str, object]:
        return self.update_manager.status().to_dict()

    def install_update(self) -> dict[str, object]:
        status = self.update_manager.install_and_restart()
        threading.Thread(target=self._close_window_for_update, daemon=True).start()
        return status.to_dict()

    def _close_window_for_update(self) -> None:
        # Give the JS bridge enough time to receive the installing state before
        # closing. The detached updater waits for this process to fully exit.
        time.sleep(0.35)
        window = self._window
        if window is not None:
            window.destroy()

    def open_external(self, url: str) -> bool:
        value = url.strip()
        if not value.startswith(("https://", "http://")):
            raise ValueError("只允许打开 http/https 地址。")
        return bool(webbrowser.open(value))

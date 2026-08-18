from __future__ import annotations

import threading
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any

from .config import LaunchConfig, NetworkConfig
from .executables import resolve_executable
from .oauth_persistence import (
    bind_server_oauth_issuer,
    canonical_oauth_issuer,
    migrate_oauth_storage_to_issuer,
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
from .user_settings import load_settings, save_settings
from .version import current_version


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
        self.permission_broker = DesktopPermissionBroker()
        self._log_lock = threading.RLock()
        self._log_cursor = 0
        self._logs: deque[dict[str, object]] = deque(maxlen=2000)
        self.manager = MCPServerManager(
            store=self.store,
            log=self._append_log,
            permission_broker=self.permission_broker,
        )
        self.update_manager = UpdateManager(log=self._append_log)
        self._latest_release = None
        self._window: Any | None = None
        self._permission_attention_id = ""
        self._migrate_legacy_desktop_settings()

    def _bind_window(self, window: Any) -> None:
        self._window = window

    def _close(self) -> None:
        self.manager.stop_all()
        self.update_manager.cleanup()
        self.permission_broker.cleanup()

    def list_permission_requests(self) -> list[dict[str, object]]:
        requests = self.permission_broker.pending()
        names = {profile.server_id: profile.name for profile in self.store.list()}
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

    def respond_permission_request(self, request_id: str, approved: bool) -> bool:
        return self.permission_broker.respond(str(request_id), bool(approved))

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

    def _migrate_legacy_desktop_settings(self) -> None:
        if self.store.list():
            return
        settings = load_settings()
        workspace = str(settings.get("workspace") or "").strip()
        if not workspace:
            return

        raw_network = settings.get("network")
        network_settings = raw_network if isinstance(raw_network, dict) else {}
        provider = str(
            settings.get("network_provider")
            or network_settings.get("provider")
            or "cloudflare"
        )
        provider_settings_raw = network_settings.get(provider)
        provider_settings = (
            provider_settings_raw if isinstance(provider_settings_raw, dict) else {}
        )

        public_url = str(provider_settings.get("public_url") or "")
        if provider == "cloudflare" and not public_url:
            public_url = str(settings.get("server_url") or "")

        options: dict[str, str] = {}
        for key in ("tunnel_token", "executable", "config_file", "authtoken"):
            value = provider_settings.get(key)
            if value:
                options[key] = str(value)
        if provider == "cloudflare" and "tunnel_token" not in options:
            value = settings.get("tunnel_token")
            if value:
                options["tunnel_token"] = str(value)

        try:
            network = NetworkConfig(
                provider=provider,
                public_url=public_url,
                options=options,
            ).validated()
            profile = self.store.create(
                name="默认服务",
                workspace=Path(workspace),
                oauth_password=str(settings.get("password") or ""),
                network=network,
                port=8234,
                lifecycle=default_lifecycle(network),
            )
            if network.public_url:
                issuer = canonical_oauth_issuer(network.public_url)
                migrate_oauth_storage_to_issuer(
                    issuer,
                    server_id=profile.server_id,
                )
                bind_server_oauth_issuer(profile.server_id, issuer)
            self._save_selected_server_id(profile.server_id)
            self._append_log(f"已将旧版桌面配置迁移为 Server Profile: {profile.name}")
        except Exception as exc:
            self._append_log(f"旧版桌面配置自动迁移失败: {exc}")

    def bootstrap(self) -> dict[str, object]:
        profiles = self.store.list()
        selected = self._selected_server_id()
        ids = {profile.server_id for profile in profiles}
        if selected not in ids:
            selected = profiles[0].server_id if profiles else ""
        return {
            "app_name": "Coding Tools MCP",
            "version": self._app_version,
            "update_download_proxy_prefix": self._update_download_proxy_prefix(),
            "selected_server_id": selected,
            "next_default_port": self.store.next_default_port(),
            "servers": [self._profile_payload(profile) for profile in profiles],
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

    def list_servers(self) -> list[dict[str, object]]:
        return [self._profile_payload(profile) for profile in self.store.list()]

    def get_next_port(self) -> int:
        return self.store.next_default_port()

    def select_server(self, server_id: str) -> bool:
        if self.store.get(server_id) is None:
            return False
        self._save_selected_server_id(server_id)
        return True

    def create_server(self, payload: dict[str, object]) -> dict[str, object]:
        network = self._network_from_payload(payload.get("network"))
        remember = bool(payload.get("remember_secrets", True))
        profile = self.store.create(
            name=str(payload.get("name") or ""),
            workspace=Path(str(payload.get("workspace") or "")),
            oauth_password=str(payload.get("oauth_password") or "") if remember else "",
            network=self._persistable_network(network, remember),
            host=str(payload.get("host") or "127.0.0.1"),
            port=int(payload.get("port") or self.store.next_default_port()),
            lifecycle=default_lifecycle(network),
            permission_mode=str(payload.get("permission_mode") or "safe"),
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
        profile = self.store.save(
            MCPServerProfile(
                server_id=current.server_id,
                name=str(payload.get("name") or current.name),
                workspace=Path(str(payload.get("workspace") or current.workspace)),
                oauth_password=(
                    str(payload.get("oauth_password") or "") if remember else ""
                ),
                network=self._persistable_network(network, remember),
                host=str(payload.get("host") or current.host),
                port=int(payload.get("port") or current.port),
                lifecycle=default_lifecycle(network),
                permission_mode=str(
                    payload.get("permission_mode") or current.permission_mode
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

    def list_oauth_clients(self, server_id: str) -> list[dict[str, object]]:
        return [
            {
                "client_id": client.client_id,
                "client_name": client.client_name or "未命名客户端",
                "redirect_uris": list(client.redirect_uris),
                "token_endpoint_auth_method": client.token_endpoint_auth_method,
                "issued_at": client.issued_at,
            }
            for client in self.manager.oauth_clients(server_id)
        ]

    def revoke_oauth_client(self, server_id: str, client_id: str) -> bool:
        return self.manager.remove_oauth_client(server_id, client_id)

    def revoke_all_oauth_clients(self, server_id: str) -> int:
        return self.manager.clear_oauth_clients(server_id)

    def get_logs(self, after: int = 0) -> dict[str, object]:
        with self._log_lock:
            entries = [entry for entry in self._logs if int(entry["id"]) > int(after)]
            return {"cursor": self._log_cursor, "entries": entries}

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

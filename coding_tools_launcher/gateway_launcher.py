from __future__ import annotations

import json
import os
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

try:
    import certifi
except ImportError:  # pragma: no cover - desktop requirements normally include it
    certifi = None

from mcp_tools_server.local_permission_broker import (
    BROKER_DIR_ENV,
    BROKER_SECRET_ENV,
    BROKER_SERVER_ID_ENV,
)
from mcp_tools_server.route_probe import (
    ROUTE_PROBE_HEADER,
    ROUTE_PROBE_PATH,
    ROUTE_PROBE_TOKEN_ENV,
    workspace_fingerprint,
)

from .config import DEFAULT_HOST, DEFAULT_PORT, LaunchConfig, NetworkConfig
from .gateway_process import (
    GatewayChildProfile,
    GatewayProcessConfig,
    GatewayServerProcess,
)
from .network import NetworkProvider, create_network_provider
from .launcher import MCPLauncher
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
    mode: str = "multi"
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
        mode = self.mode.strip().lower() or "multi"
        if mode not in {"single", "multi"}:
            raise ValueError(f"不支持的 Service mode: {mode}")
        if mode == "single" and not any(
            profile.instance_path == "" for profile in profiles
        ):
            raise ValueError("单 Workspace 模式必须包含一个根 Workspace Profile。")
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
            mode=mode,
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


@dataclass(frozen=True, slots=True)
class GatewayProfileDiagnostic:
    server_id: str
    name: str
    instance_path: str
    ok: bool
    checks: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GatewayDiagnosticReport:
    ok: bool
    public_base_url: str
    checked_at: int
    profiles: tuple[GatewayProfileDiagnostic, ...]


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
        self._direct = MCPLauncher(
            self._log,
            permission_broker=permission_broker,
        )
        self._info: GatewayLaunchInfo | None = None
        self._active_mode = "multi"
        self._single_server_id = ""
        self._stopping = False
        self._exit_reason = ""
        self._route_probe_token = ""
        self._last_diagnostic: GatewayDiagnosticReport | None = None

    def _log(self, message: str) -> None:
        self._log_callback(message)

    @property
    def info(self) -> GatewayLaunchInfo | None:
        if self._active_mode == "single" and self._direct.info is None:
            return None
        return self._info

    @property
    def exit_reason(self) -> str:
        if self._active_mode == "single":
            return self._direct.exit_reason
        return self._exit_reason

    @property
    def is_running(self) -> bool:
        if self._active_mode == "single":
            return self._direct.is_running
        provider = self._provider
        process = self._gateway.process
        return bool(
            provider
            and process
            and provider.is_running
            and process.poll() is None
            and not self._stopping
        )

    def oauth_registry_file(self, server_id: str) -> Path | None:
        if self._active_mode == "single":
            if server_id.strip() != self._single_server_id:
                return None
            return self._direct.oauth_registry_file
        return self._gateway.oauth_registry_file(server_id)

    @property
    def last_diagnostic(self) -> GatewayDiagnosticReport | None:
        return self._last_diagnostic

    @staticmethod
    def _ssl_context() -> ssl.SSLContext:
        context = ssl.create_default_context()
        if certifi is not None:
            try:
                context.load_verify_locations(cafile=certifi.where())
            except OSError:
                pass
        return context

    def _json_get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 5.0,
    ) -> tuple[dict[str, object], dict[str, str]]:
        request = urllib.request.Request(
            url,
            headers={"Cache-Control": "no-cache", **(headers or {})},
            method="GET",
        )
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=self._ssl_context(),
        ) as response:
            raw = json.loads(response.read().decode("utf-8"))
            if not isinstance(raw, dict):
                raise RuntimeError(f"诊断端点返回了非对象 JSON: {url}")
            return raw, {key.lower(): value for key, value in response.headers.items()}

    def _profile_diagnostic(
        self,
        info: GatewayLaunchInfo,
        profile: GatewayProfileLaunchInfo,
        route_probe_token: str,
    ) -> GatewayProfileDiagnostic:
        checks: list[str] = []
        errors: list[str] = []
        expected_fingerprint = workspace_fingerprint(profile.workspace)
        local_probe = (
            f"http://{info.host}:{info.port}{profile.instance_path}{ROUTE_PROBE_PATH}"
        )
        public_probe = (
            f"{info.public_base_url}{profile.instance_path}{ROUTE_PROBE_PATH}"
        )
        probe_headers = {ROUTE_PROBE_HEADER: route_probe_token}

        try:
            payload, _ = self._json_get(local_probe, headers=probe_headers)
            if payload.get("workspace_fingerprint") != expected_fingerprint:
                raise RuntimeError("本地 Path 命中了错误的 Workspace Runtime")
            checks.append("local_path_runtime")
        except Exception as exc:  # noqa: BLE001 - aggregate diagnostic failures
            errors.append(f"local_path_runtime: {exc}")

        try:
            payload, _ = self._json_get(public_probe, headers=probe_headers)
            if payload.get("workspace_fingerprint") != expected_fingerprint:
                raise RuntimeError("公网 Path 命中了错误的 Workspace Runtime")
            checks.append("public_path_runtime")
        except Exception as exc:  # noqa: BLE001 - aggregate diagnostic failures
            errors.append(f"public_path_runtime: {exc}")

        try:
            card, _ = self._json_get(
                f"{info.public_base_url}{profile.instance_path}/"
            )
            transport = card.get("transport")
            endpoint = transport.get("endpoint") if isinstance(transport, dict) else None
            if endpoint != f"{profile.instance_path}/mcp":
                raise RuntimeError(f"Server Card endpoint 不匹配: {endpoint}")
            checks.append("server_card")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"server_card: {exc}")

        authorization_metadata_url = (
            f"{info.public_base_url}/.well-known/oauth-authorization-server"
            f"{profile.instance_path}"
        )
        try:
            metadata, _ = self._json_get(authorization_metadata_url)
            expected_issuer = profile.oauth_issuer
            expected_authorize = f"{expected_issuer}/oauth/authorize"
            expected_token = f"{expected_issuer}/oauth/token"
            if metadata.get("issuer") != expected_issuer:
                raise RuntimeError(f"OAuth issuer 不匹配: {metadata.get('issuer')}")
            if metadata.get("authorization_endpoint") != expected_authorize:
                raise RuntimeError("OAuth authorization_endpoint 不匹配")
            if metadata.get("token_endpoint") != expected_token:
                raise RuntimeError("OAuth token_endpoint 不匹配")
            checks.append("oauth_authorization_metadata")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"oauth_authorization_metadata: {exc}")

        protected_metadata_url = (
            f"{info.public_base_url}/.well-known/oauth-protected-resource"
            f"{profile.instance_path}/mcp"
        )
        try:
            metadata, _ = self._json_get(protected_metadata_url)
            if metadata.get("resource") != profile.public_mcp_url:
                raise RuntimeError(f"OAuth protected resource 不匹配: {metadata.get('resource')}")
            authorization_servers = metadata.get("authorization_servers")
            if not isinstance(authorization_servers, list) or profile.oauth_issuer not in authorization_servers:
                raise RuntimeError("OAuth authorization_servers 未包含当前 Profile issuer")
            checks.append("oauth_protected_resource")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"oauth_protected_resource: {exc}")

        try:
            request = urllib.request.Request(
                profile.public_mcp_url,
                headers={"Cache-Control": "no-cache"},
                method="GET",
            )
            try:
                urllib.request.urlopen(
                    request,
                    timeout=5.0,
                    context=self._ssl_context(),
                ).close()
                raise RuntimeError("未授权 MCP GET 意外成功")
            except urllib.error.HTTPError as exc:
                if exc.code != 401:
                    raise RuntimeError(f"未授权 MCP GET 返回 HTTP {exc.code}") from exc
                challenge = exc.headers.get("WWW-Authenticate", "")
                if protected_metadata_url not in challenge:
                    raise RuntimeError("WWW-Authenticate resource_metadata 不匹配")
            checks.append("mcp_auth_challenge")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"mcp_auth_challenge: {exc}")

        return GatewayProfileDiagnostic(
            server_id=profile.server_id,
            name=profile.name,
            instance_path=profile.instance_path,
            ok=not errors,
            checks=tuple(checks),
            errors=tuple(errors),
        )

    def diagnose(self) -> GatewayDiagnosticReport:
        with self._lock:
            if self._active_mode == "single":
                raise RuntimeError("单 Workspace 模式不需要多 Profile Gateway 自检。")
            if not self.is_running or self._info is None:
                raise RuntimeError("请先启动 Local MCP Gateway，再执行公网自检。")
            info = self._info
            route_probe_token = self._route_probe_token
        if not route_probe_token:
            raise RuntimeError("当前 Gateway Session 没有可用的内部诊断 Token。")
        profiles = tuple(
            self._profile_diagnostic(info, profile, route_probe_token)
            for profile in info.profiles
        )
        report = GatewayDiagnosticReport(
            ok=bool(profiles) and all(profile.ok for profile in profiles),
            public_base_url=info.public_base_url,
            checked_at=int(time.time()),
            profiles=profiles,
        )
        with self._lock:
            if self._info is info:
                self._last_diagnostic = report
        return report

    def _diagnose_background(self, attempts: int = 8) -> None:
        last_report: GatewayDiagnosticReport | None = None
        for attempt in range(1, attempts + 1):
            with self._lock:
                if self._stopping or self._info is None:
                    return
            try:
                report = self.diagnose()
            except Exception as exc:  # noqa: BLE001 - diagnostic must be non-fatal
                if attempt == attempts:
                    self._log(f"警告：Gateway 公网自检无法完成：{exc}")
                else:
                    time.sleep(0.75)
                continue
            last_report = report
            if report.ok:
                self._log(
                    f"Gateway 公网 E2E 自检通过：{len(report.profiles)} 个 Profile "
                    "的 Path、Runtime 与 OAuth metadata 均匹配。"
                )
                return
            if attempt < attempts:
                time.sleep(0.75)
        if last_report is not None:
            failed = [profile.name for profile in last_report.profiles if not profile.ok]
            self._log(
                "警告：Gateway 公网 E2E 自检未通过，但 Gateway 保持运行。"
                f"失败 Profile: {', '.join(failed) or '未知'}"
            )

    def start(self, config: GatewayLaunchConfig) -> GatewayLaunchInfo:
        with self._lock:
            if self.is_running:
                raise RuntimeError("Local MCP Gateway 已经在运行。")
            validated = config.validated()
            if validated.mode == "single":
                return self._start_single(validated)
            self._active_mode = "multi"
            self._single_server_id = ""
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
                self._route_probe_token = secrets.token_urlsafe(32)
                self._last_diagnostic = None
                env[ROUTE_PROBE_TOKEN_ENV] = self._route_probe_token

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
                if network_info.mode_label == "Cloudflare Named Tunnel":
                    threading.Thread(
                        target=self._diagnose_background,
                        daemon=True,
                    ).start()
                return self._info
            except Exception:
                self._stop_locked()
                raise

    def _start_single(self, config: GatewayLaunchConfig) -> GatewayLaunchInfo:
        root = next(
            (profile for profile in config.profiles if profile.instance_path == ""),
            None,
        )
        if root is None:
            raise ValueError("单 Workspace 模式缺少根 Workspace Profile。")
        lifecycle = "ephemeral" if _ephemeral_network(config.network) else root.lifecycle
        self._active_mode = "single"
        self._single_server_id = root.server_id
        self._stopping = False
        self._exit_reason = ""
        self._last_diagnostic = None
        direct_info = self._direct.start(
            LaunchConfig(
                workspace=root.workspace,
                oauth_password=root.oauth_password,
                network=config.network,
                host=config.host,
                port=config.port,
                server_id=root.server_id,
                lifecycle=lifecycle,
                permission_mode=root.permission_mode,
                allow_network=root.allow_network,
                enable_view_image=root.enable_view_image,
            )
        )
        profile_info = GatewayProfileLaunchInfo(
            server_id=root.server_id,
            name=root.name,
            workspace=root.workspace,
            instance_path="",
            local_mcp_url=direct_info.local_mcp_url,
            public_mcp_url=direct_info.public_mcp_url,
            oauth_issuer=direct_info.public_base_url,
            lifecycle=lifecycle,
        )
        self._info = GatewayLaunchInfo(
            host=config.host,
            port=config.port,
            public_base_url=direct_info.public_base_url,
            tunnel_url=direct_info.tunnel_url,
            url_mode=direct_info.url_mode,
            profiles=(profile_info,),
        )
        self._log(
            "服务以单 Workspace 模式启动；已保存的子 Profile 本次不参与运行。"
        )
        return self._info

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
        if self._active_mode == "single":
            self._direct.stop()
        else:
            self._gateway.stop()
        if self._provider is not None:
            self._provider.stop()
        self._provider = None
        self._info = None
        self._route_probe_token = ""
        self._last_diagnostic = None
        self._single_server_id = ""

    def wait(self) -> None:
        while self.is_running:
            time.sleep(0.5)


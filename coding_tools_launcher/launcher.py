from __future__ import annotations

import os
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

try:
    import certifi
except ImportError:  # pragma: no cover - desktop requirements normally include it
    certifi = None

from .config import LaunchConfig, LaunchInfo
from .mcp_process import MCPServerProcess
from .network import NetworkProvider, create_network_provider
from .oauth_persistence import (
    OAUTH_REGISTRY_FILE_ENV,
    OAUTH_TOKEN_SECRET_ENV,
    OAuthPersistence,
    bind_server_oauth_issuer,
    canonical_oauth_issuer,
    migrate_oauth_storage_to_issuer,
    prepare_ephemeral_oauth_persistence,
    prepare_issuer_oauth_persistence,
)
from .process_utils import LogCallback, check_port_available
from mcp_tools_server.route_probe import (
    ROUTE_PROBE_HEADER,
    ROUTE_PROBE_PATH,
    ROUTE_PROBE_TOKEN_ENV,
)

if TYPE_CHECKING:
    from .permission_broker import DesktopPermissionBroker


class MCPLauncher:
    def __init__(
        self,
        log: LogCallback | None = None,
        permission_broker: "DesktopPermissionBroker | None" = None,
    ):
        self._log_callback = log or (lambda _message: None)
        self._lock = threading.RLock()
        self._provider: NetworkProvider | None = None
        self._mcp = MCPServerProcess(self._log)
        self._info: LaunchInfo | None = None
        self._oauth_persistence: OAuthPersistence | None = None
        self._stopping = False
        self._exit_reason = ""
        self._permission_broker = permission_broker

    def _log(self, message: str) -> None:
        self._log_callback(message)

    def _verify_named_tunnel_route(
        self,
        public_base_url: str,
        route_probe_token: str,
        *,
        attempts: int = 6,
    ) -> None:
        """Verify that a Named Tunnel public URL routes to this process.

        The per-process probe verifies that the configured public URL reaches
        this exact MCP process without exposing the Tunnel token or OAuth
        password.  It is only a post-start diagnostic and is not used for
        cross-machine path routing.
        """

        context = ssl.create_default_context()
        if certifi is not None:
            try:
                context.load_verify_locations(cafile=certifi.where())
            except OSError:
                pass

        base = public_base_url.rstrip("/")
        last_error = ""
        successful_probes = 0
        required_successes = 3
        for attempt in range(1, attempts + 1):
            nonce = secrets.token_urlsafe(8)
            request = urllib.request.Request(
                f"{base}{ROUTE_PROBE_PATH}?nonce={nonce}",
                headers={
                    ROUTE_PROBE_HEADER: route_probe_token,
                    "Cache-Control": "no-cache",
                    "Connection": "close",
                },
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=4.0, context=context) as response:
                    if response.status == 200:
                        successful_probes += 1
                        if successful_probes >= required_successes:
                            self._log("Cloudflare Named Tunnel 公网路由校验通过，域名已回源到当前 MCP 进程。")
                            return
                    else:
                        last_error = f"HTTP {response.status}"
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise RuntimeError(
                        "Cloudflare Named Tunnel 公网路由没有回到当前 MCP 进程。"
                        "请检查该 Public Hostname 是否绑定到当前电脑的独立 Tunnel，"
                        "并确认没有与其他电脑复用同一个 hostname/Tunnel Token。"
                    ) from exc
                if exc.code in {401, 403}:
                    self._log(
                        f"Cloudflare 公网探针收到 HTTP {exc.code}；"
                        "这通常表示 Access/WAF 等边缘安全策略拦截了内部诊断请求。"
                        "已跳过精确回源校验，不据此判断 MCP 公网连接不可用。"
                    )
                    return
                if exc.code in {502, 503, 504}:
                    last_error = f"HTTP {exc.code} Bad Gateway"
                else:
                    last_error = f"HTTP {exc.code}"
            except urllib.error.URLError as exc:
                reason = exc.reason
                if isinstance(reason, ssl.SSLCertVerificationError):
                    raise RuntimeError(
                        "Cloudflare Named Tunnel Public URL 的 TLS 证书校验失败。"
                        "请检查该域名的 Cloudflare Edge Certificate、DNS 和证书链。"
                    ) from exc
                last_error = str(reason or exc)
            except TimeoutError as exc:
                last_error = str(exc)

            if attempt < attempts:
                time.sleep(0.4)

        if successful_probes:
            raise RuntimeError(
                "Cloudflare Named Tunnel 路由校验结果不稳定：部分请求回到了当前 MCP 进程，"
                "但无法连续确认。请检查 Public Hostname、Tunnel Origin 和是否存在 Token 复用。"
            )
        raise RuntimeError(
            "Cloudflare Named Tunnel 已连接 Edge，但 Public URL 无法回源到当前 MCP Server"
            f"（{last_error or '未知错误'}）。请确认当前 Public Hostname 已绑定到"
            "这台电脑的独立 Tunnel，且该 Tunnel 的 Published Application 指向"
            " http://127.0.0.1:<MCP端口>。"
        )

    def _verify_named_tunnel_route_background(
        self,
        public_base_url: str,
        route_probe_token: str,
    ) -> None:
        """Run the public route probe as a non-fatal post-start diagnostic.

        A Named Tunnel becoming connected to Cloudflare Edge and its public
        hostname becoming reachable are two separate pieces of state.  DNS or
        the published application may lag behind or be misconfigured.  None
        of those cases should tear down an otherwise healthy local MCP process
        and tunnel.
        """

        try:
            self._verify_named_tunnel_route(public_base_url, route_probe_token)
        except Exception as exc:  # noqa: BLE001 - diagnostic must never kill startup
            self._log(
                "警告：Cloudflare 公网回源校验未通过，但 MCP Server 与 Named Tunnel "
                f"保持运行。公网 MCP 连接可能暂不可用：{exc}"
            )

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
            named_cloudflare = (
                config.network.provider == "cloudflare"
                and bool(config.network.public_url)
            )
            route_probe_token = secrets.token_urlsafe(32) if named_cloudflare else ""
            self._stopping = False
            self._exit_reason = ""
            check_port_available(config.host, config.port)
            try:
                self._provider = create_network_provider(
                    config.network.provider,
                    self._log,
                )
                network_info = self._provider.start(config.host, config.port, config.network)
                public_base_url = canonical_oauth_issuer(network_info.public_base_url)
                if config.lifecycle == "ephemeral":
                    oauth_persistence = prepare_ephemeral_oauth_persistence(
                        config.server_id or "session"
                    )
                else:
                    issuer = public_base_url
                    migrated = migrate_oauth_storage_to_issuer(
                        issuer,
                        server_id=config.server_id,
                    )
                    oauth_persistence = prepare_issuer_oauth_persistence(issuer)
                    if config.server_id:
                        bind_server_oauth_issuer(config.server_id, issuer)
                    if migrated:
                        self._log(
                            f"OAuth 状态已迁移到 issuer 身份目录: {issuer}"
                        )
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
                if route_probe_token:
                    env[ROUTE_PROBE_TOKEN_ENV] = route_probe_token
                if self._permission_broker is not None:
                    env.update(
                        self._permission_broker.child_environment(config.server_id)
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
                        "OAuth 状态持久化已启用：DCR client_id 与 token secret 按 issuer 跨重启保留。"
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
                if named_cloudflare:
                    threading.Thread(
                        target=self._verify_named_tunnel_route_background,
                        args=(public_base_url, route_probe_token),
                        daemon=True,
                    ).start()
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

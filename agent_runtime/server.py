"""HTTP/stdio entry points for the MicroMatrix Workbench Agent Runtime."""

from __future__ import annotations

import argparse
import http.server
import json
import os
import secrets
import signal
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Any

from .oauth import (
    OAUTH_MAX_BODY_BYTES,
    OAUTH_REFRESH_TOKEN_TTL_SECONDS,
    OAUTH_TOKEN_TTL_SECONDS,
    OAuthClientRegistry,
    OAuthObservedClientRegistry,
)
from .oauth_service import OAuthService
from .gateway import GatewayProfile, GatewayRuntimePool
from .gateway.config import build_gateway_runtime_pool, load_gateway_config
from .http_mcp import MCPHTTPController
from .http_oauth import OAuthHTTPController
from .core.constants import ENDPOINT_PATH
from .permissions.capabilities import PERMISSION_MODES
from .runtime import Runtime
from .route_probe import (
    ROUTE_PROBE_HEADER,
    ROUTE_PROBE_PATH,
    ROUTE_PROBE_TOKEN_ENV,
    workspace_fingerprint,
)
from .transport_stdio import serve_stdio


ENV_PREFIX = "AGENT_RUNTIME"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _loopback(host: str) -> bool:
    return host in {"", "localhost", "127.0.0.1", "::1"}


def _normalize_public_server_url(value: str | None) -> str | None:
    """Normalize a configured public MCP URL to the OAuth server base URL.

    The desktop UI accepts either ``https://host`` or
    ``https://host/mcp``.  Direct server launches should behave the same way;
    otherwise OAuthService.resource would append ENDPOINT_PATH a second time
    and advertise ``.../mcp/mcp`` as the token audience.
    """

    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            f"{ENV_PREFIX}_SERVER_URL must be a complete http/https URL"
        )
    if parsed.query or parsed.fragment:
        raise ValueError(
            f"{ENV_PREFIX}_SERVER_URL must not contain a query or fragment"
        )
    path = parsed.path.rstrip("/")
    if path.endswith(ENDPOINT_PATH):
        path = path[: -len(ENDPOINT_PATH)].rstrip("/")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, "", "")
    ).rstrip("/")


def _url_path(value: str | None) -> str:
    """Return a normalized URL path without a trailing slash."""

    if not value:
        return ""
    path = urllib.parse.urlsplit(value).path.rstrip("/")
    return path if path != "/" else ""


class MCPHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        runtime: Runtime | None = None,
        *,
        gateway_pool: GatewayRuntimePool | None = None,
    ):
        if runtime is None and gateway_pool is None:
            raise ValueError("runtime or gateway_pool is required")
        if runtime is not None and gateway_pool is not None:
            raise ValueError("runtime and gateway_pool are mutually exclusive")
        self.runtime = runtime
        self.gateway_pool = gateway_pool
        super().__init__(address, MCPHandler)


class MCPHandler(http.server.BaseHTTPRequestHandler):
    server_version = "MicroMatrixWorkbench/1"
    mcp_controller = MCPHTTPController()
    oauth_controller = OAuthHTTPController()

    @property
    def runtime(self) -> Runtime:
        selected = getattr(self, "_gateway_runtime", None)
        if selected is not None:
            return selected
        runtime = self.server.runtime  # type: ignore[attr-defined]
        if runtime is None:
            raise RuntimeError("gateway request runtime has not been selected")
        return runtime

    @property
    def gateway_profile(self) -> GatewayProfile | None:
        return getattr(self, "_gateway_profile", None)

    def _select_request_runtime(self) -> bool:
        pool = self.server.gateway_pool  # type: ignore[attr-defined]
        if pool is None:
            return True
        resolved = pool.runtime_for_path(urllib.parse.urlparse(self.path).path)
        if resolved is None:
            self._json(404, {"error": "gateway_profile_not_found"})
            return False
        profile, runtime = resolved
        self._gateway_profile = profile
        self._gateway_runtime = runtime
        return True

    def log_message(self, fmt: str, *args: Any) -> None:
        print(fmt % args, file=sys.stderr)

    def _base_url(self) -> str:
        if self.runtime.oauth_service and self.runtime.oauth_service.server_url:
            return self.runtime.oauth_service.server_url.rstrip("/")
        forwarded = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
        scheme = forwarded if forwarded in {"http", "https"} else "http"
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host")
        if not host:
            server_host, server_port = self.server.server_address[:2]  # type: ignore[attr-defined]
            host = f"{server_host}:{server_port}"
        base = f"{scheme}://{host}".rstrip("/")
        profile = self.gateway_profile
        return f"{base}{profile.instance_path}" if profile else base

    def _instance_prefix(self) -> str:
        profile = self.gateway_profile
        if profile is not None:
            return profile.instance_path
        config = self.runtime.oauth_service
        return _url_path(config.server_url if config else None)

    def _route_path(self, raw_path: str) -> str:
        prefix = self._instance_prefix()
        if prefix and raw_path == prefix:
            return "/"
        if prefix and raw_path.startswith(f"{prefix}/"):
            return raw_path[len(prefix) :]
        return raw_path

    def _resource_metadata_path(self) -> str:
        config = self.runtime.oauth_service
        resource = config.resource if config and config.resource else None
        resource_path = _url_path(resource)
        if not resource_path and self._instance_prefix():
            resource_path = f"{self._instance_prefix()}{ENDPOINT_PATH}"
        return f"/.well-known/oauth-protected-resource{resource_path}"

    def _authorization_metadata_paths(self) -> set[str]:
        prefix = self._instance_prefix()
        if not prefix:
            return {
                "/.well-known/oauth-authorization-server",
                "/.well-known/openid-configuration",
            }
        return {
            f"/.well-known/oauth-authorization-server{prefix}",
            f"/.well-known/openid-configuration{prefix}",
            f"{prefix}/.well-known/openid-configuration",
        }

    def _json(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _read(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("invalid Content-Length")
        if length < 0 or length > limit:
            raise ValueError("request body too large")
        return self.rfile.read(length)

    def _form(self, limit: int = OAUTH_MAX_BODY_BYTES) -> dict[str, str]:
        raw = self._read(limit).decode("utf-8")
        parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._select_request_runtime():
            return
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, OPTIONS")
        origin = self.headers.get("Origin")
        if origin and self.mcp_controller.allows_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, MCP-Protocol-Version, Mcp-Method, Mcp-Name")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self._select_request_runtime():
            return
        raw_path = urllib.parse.urlparse(self.path).path
        path = self._route_path(raw_path)
        if path == ROUTE_PROBE_PATH:
            expected = os.environ.get(ROUTE_PROBE_TOKEN_ENV, "").strip()
            provided = self.headers.get(ROUTE_PROBE_HEADER, "").strip()
            if not expected or not provided or not secrets.compare_digest(expected, provided):
                self._json(404, {"error": "not_found"}, {"Cache-Control": "no-store"})
                return
            self._json(
                200,
                {
                    "ok": True,
                    "workspace_fingerprint": workspace_fingerprint(
                        self.runtime.workspace.root
                    ),
                },
                {"Cache-Control": "no-store"},
            )
            return
        if path == "/":
            self._json(200, self.mcp_controller.server_card(self))
            return
        if path in {"/.well-known/mcp.json", "/.well-known/mcp/server-card.json"}:
            self._json(200, self.mcp_controller.server_card(self))
            return
        if raw_path in self._authorization_metadata_paths():
            if not self.runtime.oauth_service:
                self._json(404, {"error": "oauth_not_enabled"})
                return
            self._json(200, self.oauth_controller.authorization_metadata(self))
            return
        if raw_path == self._resource_metadata_path() or (
            not self._instance_prefix()
            and raw_path == "/.well-known/oauth-protected-resource"
        ):
            self._json(200, self.oauth_controller.protected_resource_metadata(self))
            return
        if path == "/oauth/authorize":
            self.oauth_controller.authorize_get(self)
            return
        if path == ENDPOINT_PATH:
            self.mcp_controller.get(self)
            return
        self._json(404, {"error": "not_found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._select_request_runtime():
            return
        path = self._route_path(urllib.parse.urlparse(self.path).path)
        if path != ENDPOINT_PATH:
            self._json(404, {"error": "not_found"})
            return
        self.mcp_controller.delete(self)

    def do_POST(self) -> None:  # noqa: N802
        if not self._select_request_runtime():
            return
        path = self._route_path(urllib.parse.urlparse(self.path).path)
        if path == ENDPOINT_PATH:
            self.mcp_controller.post(self)
            return
        if path == "/oauth/register":
            self.oauth_controller.register_post(self)
            return
        if path == "/oauth/authorize":
            self.oauth_controller.authorize_post(self)
            return
        if path == "/oauth/token":
            self.oauth_controller.token_post(self)
            return
        self._json(404, {"error": "not_found"})

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the MicroMatrix Workbench Agent Runtime over MCP."
    )
    parser.add_argument("--workspace", default=os.environ.get(f"{ENV_PREFIX}_WORKSPACE") or os.getcwd())
    parser.add_argument("--host", default=os.environ.get(f"{ENV_PREFIX}_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=_env_int(f"{ENV_PREFIX}_PORT", 8000))
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--auth-token", default=None)
    parser.add_argument("--oauth-mode", action="store_true", default=False)
    parser.add_argument(
        "--gateway-config",
        default=os.environ.get(f"{ENV_PREFIX}_GATEWAY_CONFIG") or None,
        help="Run Local MCP Gateway mode using a versioned local JSON config file.",
    )
    parser.add_argument("--permission-mode", choices=PERMISSION_MODES, default=None)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--enable-view-image", action="store_true", default=os.environ.get(f"{ENV_PREFIX}_ENABLE_VIEW_IMAGE", "1") != "0")
    parser.add_argument("--dangerously-skip-all-permissions", action="store_true")
    parser.add_argument("--dangerously-fake-readonly-annotations", action="store_true")
    return parser


def _permission_mode(args: argparse.Namespace) -> str:
    if args.dangerously_skip_all_permissions:
        return "dangerous"
    return args.permission_mode or os.environ.get(f"{ENV_PREFIX}_PERMISSION_MODE") or "safe"


def _oauth_service() -> OAuthService:
    password = os.environ.get(f"{ENV_PREFIX}_OAUTH_PASSWORD") or secrets.token_urlsafe(32)
    if not os.environ.get(f"{ENV_PREFIX}_OAUTH_PASSWORD"):
        print(f"OAuth authorize password: {password}", file=sys.stderr)
    server_url = _normalize_public_server_url(
        os.environ.get(f"{ENV_PREFIX}_SERVER_URL")
    )
    raw_secret = (os.environ.get(f"{ENV_PREFIX}_OAUTH_TOKEN_SECRET") or "").strip()
    if raw_secret:
        try:
            token_secret = bytes.fromhex(raw_secret)
        except ValueError as exc:
            raise ValueError(f"{ENV_PREFIX}_OAUTH_TOKEN_SECRET must be hex encoded") from exc
        if len(token_secret) < 32:
            raise ValueError(f"{ENV_PREFIX}_OAUTH_TOKEN_SECRET must contain at least 32 bytes")
    else:
        token_secret = secrets.token_bytes(32)
    token_ttl = _env_int(f"{ENV_PREFIX}_OAUTH_TOKEN_TTL", OAUTH_TOKEN_TTL_SECONDS)
    if not 60 <= token_ttl <= 604_800:
        raise ValueError(f"{ENV_PREFIX}_OAUTH_TOKEN_TTL must be between 60 and 604800")
    refresh_token_ttl = _env_int(
        f"{ENV_PREFIX}_OAUTH_REFRESH_TOKEN_TTL",
        OAUTH_REFRESH_TOKEN_TTL_SECONDS,
    )
    if not 3600 <= refresh_token_ttl <= 31_536_000:
        raise ValueError(
            f"{ENV_PREFIX}_OAUTH_REFRESH_TOKEN_TTL must be between 3600 and 31536000"
        )
    registry_file_value = (
        os.environ.get(f"{ENV_PREFIX}_OAUTH_CLIENT_REGISTRY_FILE") or ""
    ).strip()
    registry_file = Path(registry_file_value).expanduser() if registry_file_value else None
    raw_cimd_enabled = os.environ.get(f"{ENV_PREFIX}_OAUTH_CIMD_ENABLED")
    cimd_enabled = True if raw_cimd_enabled is None else _truthy(raw_cimd_enabled)
    config = OAuthService(
        password=password,
        server_url=server_url,
        token_secret=token_secret,
        cimd_enabled=cimd_enabled,
        token_ttl=token_ttl,
        refresh_token_ttl=refresh_token_ttl,
        registry=OAuthClientRegistry(registry_file),
        observed_clients=OAuthObservedClientRegistry(
            registry_file.with_name("cimd-clients.json") if registry_file else None
        ),
    )
    return config


def build_runtime(args: argparse.Namespace, *, http: bool) -> Runtime:
    permission_mode = _permission_mode(args)
    oauth_mode = bool(args.oauth_mode or _truthy(os.environ.get(f"{ENV_PREFIX}_OAUTH_MODE")) or os.environ.get(f"{ENV_PREFIX}_AUTH_MODE", "").lower() == "oauth")
    oauth = _oauth_service() if http and oauth_mode else None
    auth_token = args.auth_token or os.environ.get(f"{ENV_PREFIX}_AUTH_TOKEN") or None
    fake_readonly = bool(args.dangerously_fake_readonly_annotations or _truthy(os.environ.get(f"{ENV_PREFIX}_DANGEROUSLY_FAKE_READONLY_ANNOTATIONS")))
    return Runtime(
        Path(args.workspace),
        permission_mode=permission_mode,
        allow_network=bool(args.allow_network or _truthy(os.environ.get(f"{ENV_PREFIX}_ALLOW_NETWORK"))),
        auth_token=auth_token,
        oauth_service=oauth,
        enable_view_image=bool(args.enable_view_image),
        fake_readonly_annotations=fake_readonly,
    )


def run_http(args: argparse.Namespace) -> int:
    if args.gateway_config:
        return run_gateway_http(args)
    try:
        runtime = build_runtime(args, http=True)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    auth_mode = os.environ.get(f"{ENV_PREFIX}_AUTH_MODE", "").strip().lower()
    if not runtime.auth_enabled() and not _loopback(str(args.host)) and auth_mode != "noauth":
        print("ERROR: non-loopback HTTP binding requires authentication or AGENT_RUNTIME_AUTH_MODE=noauth.", file=sys.stderr)
        runtime.close()
        return 2
    try:
        server = MCPHTTPServer((str(args.host), int(args.port)), runtime)
    except OSError as exc:
        print(f"ERROR: cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        runtime.close()
        return 2
    print(f"MicroMatrix Workbench {runtime.server_identity()['version']} listening on http://{args.host}:{args.port}{ENDPOINT_PATH}", file=sys.stderr)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        runtime.close()
    return 0


def run_gateway_http(args: argparse.Namespace) -> int:
    try:
        config = load_gateway_config(args.gateway_config)
        registry, pool = build_gateway_runtime_pool(config)
        # Instantiate every profile before binding so invalid Workspace/OAuth
        # state fails startup atomically rather than on the first request.
        runtimes = [pool.get(profile.profile_id) for profile in registry.profiles()]
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    auth_mode = os.environ.get(f"{ENV_PREFIX}_AUTH_MODE", "").strip().lower()
    if (
        not _loopback(str(args.host))
        and auth_mode != "noauth"
        and any(not runtime.auth_enabled() for runtime in runtimes)
    ):
        print(
            "ERROR: non-loopback Gateway binding requires authentication for every profile or AGENT_RUNTIME_AUTH_MODE=noauth.",
            file=sys.stderr,
        )
        pool.close()
        return 2
    try:
        server = MCPHTTPServer(
            (str(args.host), int(args.port)),
            gateway_pool=pool,
        )
    except OSError as exc:
        print(f"ERROR: cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        pool.close()
        return 2
    print(
        f"MicroMatrix Workbench Gateway listening on http://{args.host}:{args.port} "
        f"with {len(registry)} profiles",
        file=sys.stderr,
    )
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        pool.close()
    return 0


def run_stdio(args: argparse.Namespace) -> int:
    if args.gateway_config:
        print("ERROR: Local MCP Gateway currently supports HTTP mode only.", file=sys.stderr)
        return 2
    try:
        runtime = build_runtime(args, http=False)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        return serve_stdio(runtime)
    finally:
        runtime.close()


def _install_sigterm_handler() -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    try:
        signal.signal(signal.SIGTERM, lambda signum, _frame: (_ for _ in ()).throw(SystemExit(128 + signum)))
    except (OSError, ValueError, AttributeError):
        pass


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _install_sigterm_handler()
    return run_stdio(args) if args.stdio else run_http(args)


if __name__ == "__main__":
    raise SystemExit(main())

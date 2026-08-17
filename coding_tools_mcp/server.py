"""HTTP/stdio entry points for the project-owned Coding Tools MCP server."""

from __future__ import annotations

import argparse
import base64
import html
import http.server
import json
import logging
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
    OAuthConfig,
    create_access_token,
    valid_pkce_challenge,
    validate_access_token,
    verify_pkce,
)
from .protocol import KNOWN_PROTOCOL_VERSIONS, dispatch
from .runtime import ENDPOINT_PATH, PERMISSION_MODES, Runtime
from .transport_stdio import serve_stdio


ENV_PREFIX = "CODING_TOOLS_MCP"
MAX_HTTP_BODY_BYTES = 1_048_576
LOGGER = logging.getLogger(__name__)


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


def _allowed_origin(origin: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    configured = {
        value.strip().rstrip("/")
        for value in os.environ.get(f"{ENV_PREFIX}_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    }
    return origin.rstrip("/") in configured


class MCPHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], runtime: Runtime):
        self.runtime = runtime
        super().__init__(address, MCPHandler)


class MCPHandler(http.server.BaseHTTPRequestHandler):
    server_version = "CodingToolsMCP/1"

    @property
    def runtime(self) -> Runtime:
        return self.server.runtime  # type: ignore[attr-defined, no-any-return]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(fmt % args, file=sys.stderr)

    def _base_url(self) -> str:
        if self.runtime.oauth_config and self.runtime.oauth_config.server_url:
            return self.runtime.oauth_config.server_url.rstrip("/")
        forwarded = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
        scheme = forwarded if forwarded in {"http", "https"} else "http"
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host")
        if not host:
            server_host, server_port = self.server.server_address[:2]  # type: ignore[attr-defined]
            host = f"{server_host}:{server_port}"
        return f"{scheme}://{host}".rstrip("/")

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

    def _bearer(self) -> str | None:
        value = self.headers.get("Authorization", "")
        if value.lower().startswith("bearer "):
            return value[7:].strip()
        return None

    def _mcp_auth_error(self) -> str | None:
        if not self.runtime.auth_enabled():
            return None
        token = self._bearer()
        if not token:
            return "missing_token"
        if self.runtime.auth_token and secrets.compare_digest(token, self.runtime.auth_token):
            return None
        config = self.runtime.oauth_config
        if config and validate_access_token(config, token):
            return None
        return "invalid_token"

    def _unauthorized(self, *, invalid_token: bool = False) -> None:
        base = self._base_url()
        metadata = f'{base}/.well-known/oauth-protected-resource'
        challenge = f'Bearer resource_metadata="{metadata}"'
        if invalid_token:
            challenge += ', error="invalid_token", error_description="The access token is invalid or expired."'
        self._json(
            401,
            {
                "error": "invalid_token" if invalid_token else "unauthorized",
                "error_description": (
                    "The access token is invalid or expired."
                    if invalid_token
                    else "A valid bearer token is required."
                ),
            },
            {"WWW-Authenticate": challenge},
        )

    def _server_card(self) -> dict[str, Any]:
        base = self._base_url()
        if self.runtime.oauth_config:
            auth: dict[str, Any] = {
                "type": "oauth2",
                "scheme": "Bearer",
                "authorizationUrl": f"{base}/oauth/authorize",
                "tokenUrl": f"{base}/oauth/token",
                "registrationUrl": f"{base}/oauth/register",
            }
        elif self.runtime.auth_token:
            auth = {"type": "bearer", "scheme": "Bearer"}
        else:
            auth = {"type": "none"}
        tools = self.runtime.list_tools()["tools"]
        return {
            "server": self.runtime.server_identity(),
            "supportedProtocolVersions": list(KNOWN_PROTOCOL_VERSIONS),
            "transport": {"type": "streamable_http", "endpoint": ENDPOINT_PATH, "methods": ["POST", "OPTIONS"]},
            "auth": auth,
            "capabilities": {"tools": {"listChanged": False}},
            "tools": {"count": len(tools), "names": [tool["name"] for tool in tools]},
        }

    def _oauth_metadata(self) -> dict[str, Any]:
        base = self._base_url()
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        }

    def _protected_resource_metadata(self) -> dict[str, Any]:
        base = self._base_url()
        return {
            "resource": f"{base}{ENDPOINT_PATH}",
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
        }

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        origin = self.headers.get("Origin")
        if origin and _allowed_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, MCP-Protocol-Version, Mcp-Method, Mcp-Name")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._json(200, self._server_card())
            return
        if path in {"/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"}:
            if not self.runtime.oauth_config:
                self._json(404, {"error": "oauth_not_enabled"})
                return
            self._json(200, self._oauth_metadata())
            return
        if path in {"/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"}:
            self._json(200, self._protected_resource_metadata())
            return
        if path == "/oauth/authorize":
            self._authorize_get()
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == ENDPOINT_PATH:
            self._mcp_post()
            return
        if path == "/oauth/register":
            self._register_post()
            return
        if path == "/oauth/authorize":
            self._authorize_post()
            return
        if path == "/oauth/token":
            self._token_post()
            return
        self._json(404, {"error": "not_found"})

    def _mcp_post(self) -> None:
        origin = self.headers.get("Origin")
        if origin and not _allowed_origin(origin):
            self._json(403, {"error": "origin_not_allowed"})
            return
        auth_error = self._mcp_auth_error()
        if auth_error is not None:
            self._unauthorized(invalid_token=auth_error == "invalid_token")
            return
        try:
            raw = self._read(MAX_HTTP_BODY_BYTES)
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("JSON-RPC request must be an object")
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}})
            return
        try:
            response = dispatch(self.runtime, request)
        except Exception as exc:
            # Never tear down the HTTP request because an implementation
            # exception escaped the tool/protocol boundary. A dropped MCP
            # response is surfaced by clients as opaque TaskGroup/
            # ExceptionGroup transport failures, which are not actionable.
            LOGGER.exception("Unhandled MCP dispatch failure")
            self._json(
                500,
                {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "error": {
                        "code": -32603,
                        "message": "Internal error",
                        "data": {"exception_type": type(exc).__name__},
                    },
                },
            )
            return
        if response is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json(200, response)

    def _register_post(self) -> None:
        config = self.runtime.oauth_config
        if config is None:
            self._json(404, {"error": "oauth_not_enabled"})
            return
        try:
            metadata = json.loads(self._read(OAUTH_MAX_BODY_BYTES))
            if not isinstance(metadata, dict):
                raise ValueError("registration body must be an object")
            response = config.registry.register(metadata)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid_client_metadata", "error_description": str(exc)})
            return
        self._json(201, response)

    def _validate_authorize(self, params: dict[str, str]) -> tuple[Any, str] | tuple[None, str]:
        config = self.runtime.oauth_config
        if config is None:
            return None, "OAuth is not enabled"
        client_id = params.get("client_id", "")
        client = config.registry.get(client_id)
        if client is None:
            return None, "Unknown client_id"
        redirect_uri = params.get("redirect_uri", "")
        if redirect_uri not in client.redirect_uris:
            return None, "redirect_uri is not registered"
        if params.get("response_type") != "code":
            return None, "response_type must be code"
        if params.get("code_challenge_method") != "S256":
            return None, "code_challenge_method must be S256"
        challenge = params.get("code_challenge", "")
        if not valid_pkce_challenge(challenge):
            return None, "invalid code_challenge"
        return client, ""

    def _authorize_page(self, params: dict[str, str], error: str = "") -> str:
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(value, quote=True)}">'
            for key, value in params.items()
            if key != "password"
        )
        error_html = f'<p style="color:#b42318">{html.escape(error)}</p>' if error else ""
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize Coding Tools MCP</title>
<style>body{{font-family:system-ui,sans-serif;max-width:520px;margin:60px auto;padding:0 20px}}input,button{{box-sizing:border-box;width:100%;padding:12px;margin:8px 0}}small{{color:#666}}</style></head>
<body><h1>Authorize Coding Tools MCP</h1><p>A client is requesting access to the configured workspace.</p>{error_html}
<form method="post" action="/oauth/authorize">{hidden}<label>Password<input type="password" name="password" autocomplete="current-password" required></label><button type="submit">Authorize</button></form>
<small>Only authorize clients you trust. MCP tools may read files, modify source code, and execute commands according to the configured permission mode.</small></body></html>"""

    def _authorize_get(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query, keep_blank_values=True)
        params = {key: values[-1] if values else "" for key, values in query.items()}
        _, error = self._validate_authorize(params)
        if error:
            self._html(400, self._authorize_page(params, error))
            return
        self._html(200, self._authorize_page(params))

    def _authorize_post(self) -> None:
        config = self.runtime.oauth_config
        if config is None:
            self._json(404, {"error": "oauth_not_enabled"})
            return
        try:
            params = self._form()
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"error": "invalid_request", "error_description": str(exc)})
            return
        _, error = self._validate_authorize(params)
        if error:
            self._html(400, self._authorize_page(params, error))
            return
        if not secrets.compare_digest(params.get("password", ""), config.password):
            self._html(401, self._authorize_page(params, "Incorrect password"))
            return
        requested_resource = params.get("resource", "").strip()
        if requested_resource and config.resource and requested_resource != config.resource:
            self._html(400, self._authorize_page(params, "resource does not match this MCP server"))
            return
        code = config.issue_code(
            params["client_id"],
            params["redirect_uri"],
            params["code_challenge"],
            requested_resource or config.resource,
        )
        parsed = urllib.parse.urlparse(params["redirect_uri"])
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query.append(("code", code))
        if params.get("state"):
            query.append(("state", params["state"]))
        location = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _basic_client(self) -> tuple[str | None, str | None]:
        authorization = self.headers.get("Authorization", "")
        if not authorization.lower().startswith("basic "):
            return None, None
        try:
            decoded = base64.b64decode(authorization[6:].strip()).decode("utf-8")
            client_id, client_secret = decoded.split(":", 1)
            return urllib.parse.unquote(client_id), urllib.parse.unquote(client_secret)
        except (ValueError, UnicodeDecodeError):
            return None, None

    def _token_post(self) -> None:
        config = self.runtime.oauth_config
        if config is None:
            self._json(404, {"error": "oauth_not_enabled"})
            return
        try:
            params = self._form()
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"error": "invalid_request", "error_description": str(exc)})
            return
        grant_type = params.get("grant_type")
        if grant_type == "refresh_token":
            self._refresh_token_post(config, params)
            return
        if grant_type != "authorization_code":
            self._json(400, {"error": "unsupported_grant_type"})
            return
        code = config.consume_code(params.get("code", ""))
        if code is None:
            self._json(400, {"error": "invalid_grant", "error_description": "authorization code is invalid or expired"})
            return
        client_id = params.get("client_id", "")
        basic_id, basic_secret = self._basic_client()
        if basic_id:
            client_id = basic_id
            client_secret = basic_secret
            auth_method = "client_secret_basic"
        else:
            client_secret = params.get("client_secret")
            client = config.registry.get(client_id)
            auth_method = client.token_endpoint_auth_method if client else "none"
        if client_id != code.client_id:
            self._json(400, {"error": "invalid_grant"})
            return
        client = config.registry.get(client_id)
        if client is None:
            self._json(401, {"error": "invalid_client"})
            return
        if client.token_endpoint_auth_method != "none" and not config.registry.authenticates(client_id, client_secret, auth_method):
            self._json(401, {"error": "invalid_client"})
            return
        if params.get("redirect_uri") != code.redirect_uri:
            self._json(400, {"error": "invalid_grant", "error_description": "redirect_uri mismatch"})
            return
        requested_resource = params.get("resource", "").strip()
        if requested_resource and code.resource and requested_resource != code.resource:
            self._json(400, {"error": "invalid_target", "error_description": "resource mismatch"})
            return
        verifier = params.get("code_verifier", "")
        if not verify_pkce(verifier, code.challenge):
            self._json(400, {"error": "invalid_grant", "error_description": "PKCE verification failed"})
            return
        token = create_access_token(config, client_id)
        refresh_token = config.issue_refresh_token(client_id, code.resource)
        self._json(
            200,
            {
                "access_token": token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "expires_in": config.token_ttl,
            },
        )

    def _refresh_token_post(self, config: OAuthConfig, params: dict[str, str]) -> None:
        client_id = params.get("client_id", "")
        basic_id, basic_secret = self._basic_client()
        if basic_id:
            client_id = basic_id
            client_secret = basic_secret
            auth_method = "client_secret_basic"
        else:
            client_secret = params.get("client_secret")
            client = config.registry.get(client_id)
            auth_method = client.token_endpoint_auth_method if client else "none"
        client = config.registry.get(client_id)
        if client is None:
            self._json(401, {"error": "invalid_client"})
            return
        if client.token_endpoint_auth_method != "none" and not config.registry.authenticates(
            client_id,
            client_secret,
            auth_method,
        ):
            self._json(401, {"error": "invalid_client"})
            return
        refresh_token = params.get("refresh_token", "")
        requested_resource = params.get("resource", "").strip() or config.resource
        grant = config.consume_refresh_token(
            refresh_token,
            client_id=client_id,
            resource=requested_resource,
        )
        if grant is None:
            self._json(
                400,
                {
                    "error": "invalid_grant",
                    "error_description": "refresh token is invalid, expired, or already used",
                },
            )
            return
        access_token = create_access_token(config, client_id)
        rotated_refresh_token = config.issue_refresh_token(client_id, grant.resource)
        self._json(
            200,
            {
                "access_token": access_token,
                "refresh_token": rotated_refresh_token,
                "token_type": "Bearer",
                "expires_in": config.token_ttl,
            },
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve workspace-confined coding tools over MCP.")
    parser.add_argument("--workspace", default=os.environ.get(f"{ENV_PREFIX}_WORKSPACE") or os.getcwd())
    parser.add_argument("--host", default=os.environ.get(f"{ENV_PREFIX}_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=_env_int(f"{ENV_PREFIX}_PORT", 8000))
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--auth-token", default=None)
    parser.add_argument("--oauth-mode", action="store_true", default=False)
    parser.add_argument("--permission-mode", choices=PERMISSION_MODES, default=None)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--enable-view-image", action="store_true", default=os.environ.get(f"{ENV_PREFIX}_ENABLE_VIEW_IMAGE", "1") != "0")
    parser.add_argument("--dangerously-skip-all-permissions", action="store_true")
    parser.add_argument("--dangerously-fake-readonly-annotations", action="store_true")
    # Kept as a compatibility CLI option. This implementation uses a fixed,
    # intentionally small environment baseline in safe/trusted modes.
    parser.add_argument("--shell-env-inherit", choices=("core", "all", "none"), default=None)
    return parser


def _permission_mode(args: argparse.Namespace) -> str:
    if args.dangerously_skip_all_permissions:
        return "dangerous"
    return args.permission_mode or os.environ.get(f"{ENV_PREFIX}_PERMISSION_MODE") or "safe"


def _oauth_config() -> OAuthConfig:
    password = os.environ.get(f"{ENV_PREFIX}_OAUTH_PASSWORD") or secrets.token_urlsafe(32)
    if not os.environ.get(f"{ENV_PREFIX}_OAUTH_PASSWORD"):
        print(f"OAuth authorize password: {password}", file=sys.stderr)
    server_url = (os.environ.get(f"{ENV_PREFIX}_SERVER_URL") or "").strip().rstrip("/") or None
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
    config = OAuthConfig(
        password=password,
        server_url=server_url,
        token_secret=token_secret,
        token_ttl=token_ttl,
        refresh_token_ttl=refresh_token_ttl,
    )
    client_id = os.environ.get(f"{ENV_PREFIX}_OAUTH_CLIENT_ID")
    if client_id:
        redirects = tuple(
            value.strip()
            for value in (os.environ.get(f"{ENV_PREFIX}_OAUTH_REDIRECT_URIS") or "http://127.0.0.1/callback").split(",")
            if value.strip()
        )
        config.registry.add_preregistered(
            client_id,
            redirects,
            client_secret=os.environ.get(f"{ENV_PREFIX}_OAUTH_CLIENT_SECRET") or None,
        )
    return config


def build_runtime(args: argparse.Namespace, *, http: bool) -> Runtime:
    permission_mode = _permission_mode(args)
    oauth_mode = bool(args.oauth_mode or _truthy(os.environ.get(f"{ENV_PREFIX}_OAUTH_MODE")) or os.environ.get(f"{ENV_PREFIX}_AUTH_MODE", "").lower() == "oauth")
    oauth = _oauth_config() if http and oauth_mode else None
    auth_token = args.auth_token or os.environ.get(f"{ENV_PREFIX}_AUTH_TOKEN") or None
    fake_readonly = bool(args.dangerously_fake_readonly_annotations or _truthy(os.environ.get(f"{ENV_PREFIX}_DANGEROUSLY_FAKE_READONLY_ANNOTATIONS")))
    return Runtime(
        Path(args.workspace),
        permission_mode=permission_mode,
        allow_network=bool(args.allow_network or _truthy(os.environ.get(f"{ENV_PREFIX}_ALLOW_NETWORK"))),
        auth_token=auth_token,
        oauth_config=oauth,
        enable_view_image=bool(args.enable_view_image),
        fake_readonly_annotations=fake_readonly,
    )


def run_http(args: argparse.Namespace) -> int:
    try:
        runtime = build_runtime(args, http=True)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    auth_mode = os.environ.get(f"{ENV_PREFIX}_AUTH_MODE", "").strip().lower()
    if not runtime.auth_enabled() and not _loopback(str(args.host)) and auth_mode != "noauth":
        print("ERROR: non-loopback HTTP binding requires authentication or CODING_TOOLS_MCP_AUTH_MODE=noauth.", file=sys.stderr)
        runtime.close()
        return 2
    try:
        server = MCPHTTPServer((str(args.host), int(args.port)), runtime)
    except OSError as exc:
        print(f"ERROR: cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        runtime.close()
        return 2
    print(f"Coding Tools MCP {runtime.server_identity()['version']} listening on http://{args.host}:{args.port}{ENDPOINT_PATH}", file=sys.stderr)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        runtime.close()
    return 0


def run_stdio(args: argparse.Namespace) -> int:
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

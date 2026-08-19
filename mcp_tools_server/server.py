"""HTTP/stdio entry points for the project-owned Coding Tools MCP server."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.client
import http.server
import ipaddress
import json
import logging
import os
import secrets
import signal
import socket
import ssl
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    import certifi
except ImportError:  # pragma: no cover - source-only minimal installs may omit it
    certifi = None

from .oauth import (
    access_token_client_id,
    OAUTH_MAX_BODY_BYTES,
    OAUTH_REFRESH_TOKEN_TTL_SECONDS,
    OAUTH_TOKEN_TTL_SECONDS,
    OAuthClient,
    OAuthConfig,
    client_from_metadata_document,
    create_access_token,
    is_client_id_metadata_url,
    valid_pkce_challenge,
    validate_access_token,
    verify_pkce,
)
from .errors import RpcError
from .protocol import (
    HEADER_MISMATCH,
    KNOWN_PROTOCOL_VERSIONS,
    LEGACY_PROTOCOL_VERSIONS,
    dispatch,
    rpc_response_status,
    validate_mirror_headers,
)
from .runtime import ENDPOINT_PATH, PERMISSION_MODES, Runtime
from .route_probe import ROUTE_PROBE_HEADER, ROUTE_PROBE_PATH, ROUTE_PROBE_TOKEN_ENV
from .transport_stdio import serve_stdio


ENV_PREFIX = "CODING_TOOLS_MCP"
MAX_HTTP_BODY_BYTES = 1_048_576
LOGGER = logging.getLogger(__name__)
CIMD_MAX_BYTES = 64 * 1024
CIMD_TIMEOUT_SECONDS = 5.0
CIMD_DEFAULT_CACHE_SECONDS = 300
CIMD_MAX_CACHE_SECONDS = 3600
CIMD_MAX_REDIRECTS = 3
_TUN_FAKE_IPV4_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_TUN_FAKE_IPV6_NETWORKS = (
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("::ffff:0:0:0/96"),
)


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


def _is_tun_fake_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Recognize RFC 2544 addresses used by Clash/sing-box fake-IP DNS."""

    if isinstance(address, ipaddress.IPv4Address):
        return address in _TUN_FAKE_IPV4_NETWORK
    for network in _TUN_FAKE_IPV6_NETWORKS:
        if address in network:
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
            return embedded in _TUN_FAKE_IPV4_NETWORK
    return False


def _safe_cimd_destination(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if _is_tun_fake_ip(address):
        return True
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def _public_ip_for_host(host: str, port: int) -> str:
    """Resolve CIMD safely, including transparent-proxy fake-IP answers.

    Clash/sing-box style TUN DNS uses RFC 2544 ``198.18.0.0/15`` placeholders
    for public hostnames. They remain safe here because the request is HTTPS,
    the original hostname is retained for SNI/certificate verification, and
    all ordinary private, loopback, link-local and reserved ranges stay denied.
    """

    try:
        parsed_ip = ipaddress.ip_address(host)
        addresses = [parsed_ip]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("CIMD hostname could not be resolved") from exc
        addresses = []
        for info in infos:
            raw = info[4][0]
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if address not in addresses:
                addresses.append(address)
    if not addresses:
        raise ValueError("CIMD hostname resolved to no usable address")
    # Reject the entire hostname if any answer points at an unsafe destination.
    # This prevents a hostname with mixed public/private answers from being
    # used for SSRF while accepting a TUN's paired v4/v6 fake-IP records.
    if not all(_safe_cimd_destination(address) for address in addresses):
        raise ValueError("CIMD metadata URL must resolve only to public IP addresses")
    return str(addresses[0])


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that pins the validated DNS result while keeping SNI."""

    def __init__(self, host: str, port: int, connect_ip: str, timeout: float):
        context = ssl.create_default_context()
        # A frozen PyInstaller application does not reliably inherit the host
        # Python/OpenSSL CA search paths on macOS. The desktop distribution
        # already ships certifi, so explicitly add its Mozilla CA bundle while
        # preserving any system trust roots loaded above. This keeps CIMD HTTPS
        # verification strict instead of disabling certificate checks.
        if certifi is not None:
            context.load_verify_locations(cafile=certifi.where())
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._connect_ip = connect_ip

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._connect_ip, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _cache_seconds(headers: Any) -> int:
    cache_control = str(headers.get("Cache-Control", ""))
    for item in cache_control.split(","):
        key, separator, value = item.strip().partition("=")
        if separator and key.lower() == "max-age":
            try:
                return max(0, min(int(value.strip().strip('"')), CIMD_MAX_CACHE_SECONDS))
            except ValueError:
                break
    return CIMD_DEFAULT_CACHE_SECONDS


def _fetch_cimd_document(client_id: str) -> tuple[dict[str, Any], int]:
    """Fetch a CIMD document with HTTPS, DNS pinning and bounded redirects."""

    current = client_id
    for _ in range(CIMD_MAX_REDIRECTS + 1):
        parsed = urllib.parse.urlsplit(current)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("CIMD metadata URL must be an HTTPS URL without credentials or fragment")
        port = parsed.port or 443
        connect_ip = _public_ip_for_host(parsed.hostname, port)
        connection = _PinnedHTTPSConnection(
            parsed.hostname,
            port,
            connect_ip,
            CIMD_TIMEOUT_SECONDS,
        )
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Coding-Tools-MCP-CIMD/1",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location", "").strip()
                response.read()
                if not location:
                    raise ValueError("CIMD redirect is missing Location")
                current = urllib.parse.urljoin(current, location)
                continue
            if response.status != 200:
                response.read()
                raise ValueError(f"CIMD metadata returned HTTP {response.status}")
            raw = response.read(CIMD_MAX_BYTES + 1)
            if len(raw) > CIMD_MAX_BYTES:
                raise ValueError("CIMD metadata document is too large")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("CIMD metadata is not valid UTF-8 JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError("CIMD metadata must be a JSON object")
            return payload, _cache_seconds(response.headers)
        finally:
            connection.close()
    raise ValueError("CIMD metadata redirected too many times")


def _resolve_oauth_client(config: OAuthConfig, client_id: str) -> OAuthClient | None:
    registered = config.registry.get(client_id)
    if registered is not None:
        return registered
    if not is_client_id_metadata_url(client_id):
        return None
    now = time.monotonic()
    with config.lock:
        cached = config.cimd_cache.get(client_id)
        if cached is not None and cached[1] > now:
            return cached[0]
    metadata, ttl = _fetch_cimd_document(client_id)
    client = client_from_metadata_document(client_id, metadata)
    with config.lock:
        config.cimd_cache[client_id] = (client, now + ttl)
    return client


def _normalize_public_server_url(value: str | None) -> str | None:
    """Normalize a configured public MCP URL to the OAuth server base URL.

    The desktop UI accepts either ``https://host`` or
    ``https://host/mcp``.  Direct server launches should behave the same way;
    otherwise OAuthConfig.resource would append ENDPOINT_PATH a second time
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


def _protected_resource_metadata_url(resource: str) -> str:
    """Build the RFC 9728 well-known URL for a concrete resource URI."""

    parsed = urllib.parse.urlsplit(resource)
    resource_path = parsed.path.rstrip("/")
    metadata_path = "/.well-known/oauth-protected-resource"
    if resource_path:
        metadata_path += resource_path
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, metadata_path, "", "")
    )


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

    def _instance_prefix(self) -> str:
        config = self.runtime.oauth_config
        return _url_path(config.server_url if config else None)

    def _route_path(self, raw_path: str) -> str:
        prefix = self._instance_prefix()
        if prefix and raw_path == prefix:
            return "/"
        if prefix and raw_path.startswith(f"{prefix}/"):
            return raw_path[len(prefix) :]
        return raw_path

    def _resource_metadata_path(self) -> str:
        config = self.runtime.oauth_config
        resource = config.resource if config and config.resource else None
        return f"/.well-known/oauth-protected-resource{_url_path(resource)}"

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

    def _mcp_principal(self) -> str:
        token = self._bearer()
        if not token:
            return "anonymous"
        config = self.runtime.oauth_config
        if config:
            client_id = access_token_client_id(config, token)
            if client_id:
                return f"oauth-client:{client_id}"
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _unauthorized(self, *, invalid_token: bool = False) -> None:
        base = self._base_url()
        config = self.runtime.oauth_config
        resource = config.resource if config and config.resource else f"{base}{ENDPOINT_PATH}"
        metadata = _protected_resource_metadata_url(resource)
        challenge = (
            'Bearer realm="coding-tools-mcp", '
            f'resource_metadata="{metadata}"'
        )
        if invalid_token:
            challenge += ', error="invalid_token", error_description="The access token is invalid or expired."'
        message = (
            "The access token is invalid or expired."
            if invalid_token
            else "Unauthorized"
        )
        self._json(
            401,
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32000,
                    "message": message,
                    "data": {
                        "reason": "invalid_token" if invalid_token else "missing_token"
                    },
                },
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
        config = self.runtime.oauth_config
        endpoint = _url_path(config.resource if config and config.resource else None) or ENDPOINT_PATH
        return {
            "server": self.runtime.server_identity(),
            "supportedProtocolVersions": list(KNOWN_PROTOCOL_VERSIONS),
            "transport": {"type": "streamable_http", "endpoint": endpoint, "methods": ["POST", "OPTIONS"]},
            "auth": auth,
            "capabilities": {"tools": {"listChanged": False}},
            "tools": {"count": len(tools), "names": [tool["name"] for tool in tools]},
        }

    def _oauth_metadata(self) -> dict[str, Any]:
        base = self._base_url()
        metadata: dict[str, Any] = {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "client_id_metadata_document_supported": True,
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "scopes_supported": ["mcp", "offline_access"],
        }
        config = self.runtime.oauth_config
        if config and config.resource:
            metadata["protected_resources"] = [config.resource]
        return metadata

    def _protected_resource_metadata(self) -> dict[str, Any]:
        base = self._base_url()
        config = self.runtime.oauth_config
        resource = config.resource if config and config.resource else base
        return {
            "resource": resource,
            "resource_name": "Coding Tools MCP",
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
        }

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, OPTIONS")
        origin = self.headers.get("Origin")
        if origin and _allowed_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, MCP-Protocol-Version, Mcp-Method, Mcp-Name")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        raw_path = urllib.parse.urlparse(self.path).path
        path = self._route_path(raw_path)
        if path == ROUTE_PROBE_PATH:
            expected = os.environ.get(ROUTE_PROBE_TOKEN_ENV, "").strip()
            provided = self.headers.get(ROUTE_PROBE_HEADER, "").strip()
            if not expected or not provided or not secrets.compare_digest(expected, provided):
                self._json(404, {"error": "not_found"}, {"Cache-Control": "no-store"})
                return
            self._json(200, {"ok": True}, {"Cache-Control": "no-store"})
            return
        if path == "/":
            self._json(200, self._server_card())
            return
        if path in {"/.well-known/mcp.json", "/.well-known/mcp/server-card.json"}:
            self._json(200, self._server_card())
            return
        if raw_path in self._authorization_metadata_paths():
            if not self.runtime.oauth_config:
                self._json(404, {"error": "oauth_not_enabled"})
                return
            self._json(200, self._oauth_metadata())
            return
        if raw_path == self._resource_metadata_path() or (
            not self._instance_prefix()
            and raw_path == "/.well-known/oauth-protected-resource"
        ):
            self._json(200, self._protected_resource_metadata())
            return
        if path == "/oauth/authorize":
            self._authorize_get()
            return
        if path == ENDPOINT_PATH:
            auth_error = self._mcp_auth_error()
            if auth_error is not None:
                self._unauthorized(invalid_token=auth_error == "invalid_token")
                return
            self._json(
                405,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": "SSE GET stream is not supported",
                    },
                },
                {"Allow": "POST"},
            )
            return
        self._json(404, {"error": "not_found"})

    def do_DELETE(self) -> None:  # noqa: N802
        path = self._route_path(urllib.parse.urlparse(self.path).path)
        if path != ENDPOINT_PATH:
            self._json(404, {"error": "not_found"})
            return
        auth_error = self._mcp_auth_error()
        if auth_error is not None:
            self._unauthorized(invalid_token=auth_error == "invalid_token")
            return
        self._json(
            405,
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32601,
                    "message": "DELETE is not supported: this endpoint has no sessions to terminate",
                },
            },
            {"Allow": "POST"},
        )

    def _duplicate_mirror_header(self) -> str | None:
        for header in ("MCP-Protocol-Version", "Mcp-Method", "Mcp-Name"):
            if len(self.headers.get_all(header) or ()) > 1:
                return header
        return None

    def do_POST(self) -> None:  # noqa: N802
        path = self._route_path(urllib.parse.urlparse(self.path).path)
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
        if self.headers.get_content_type().lower() != "application/json":
            self._json(
                415,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Content-Type must be application/json"},
                },
            )
            return
        if self.headers.get("Content-Length") is None:
            self._json(
                411,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Content-Length is required"},
                },
            )
            return
        try:
            raw = self._read(MAX_HTTP_BODY_BYTES)
            request = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}})
            return
        if isinstance(request, list):
            self._json(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32600,
                        "message": "JSON-RPC batch requests are not supported by Streamable HTTP",
                    },
                },
            )
            return
        if not isinstance(request, dict):
            self._json(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Request"},
                },
            )
            return
        method = request.get("method")
        raw_params = request.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        protocol_version = self.headers.get("MCP-Protocol-Version")
        duplicate = self._duplicate_mirror_header()
        if duplicate is not None:
            self._json(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "error": {
                        "code": HEADER_MISMATCH,
                        "message": f"{duplicate} must appear exactly once",
                        "data": {"header": duplicate, "reason": "duplicate"},
                    },
                },
            )
            return
        if isinstance(method, str):
            try:
                validate_mirror_headers(
                    method,
                    params,
                    version_header=protocol_version,
                    method_header=self.headers.get("Mcp-Method"),
                    name_header=self.headers.get("Mcp-Name"),
                )
            except RpcError as exc:
                self._json(
                    400,
                    {
                        "jsonrpc": "2.0",
                        "id": request.get("id"),
                        "error": {
                            "code": exc.code,
                            "message": exc.message,
                            **({"data": exc.data} if exc.data is not None else {}),
                        },
                    },
                )
                return
        if (
            protocol_version
            and protocol_version not in KNOWN_PROTOCOL_VERSIONS
            and not (isinstance(params.get("_meta"), dict) and "io.modelcontextprotocol/protocolVersion" in params["_meta"])
        ):
            self._json(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "error": {
                        "code": -32600,
                        "message": "Unsupported MCP protocol version",
                        "data": {"supported": list(KNOWN_PROTOCOL_VERSIONS), "received": protocol_version},
                    },
                },
            )
            return
        try:
            response = dispatch(
                self.runtime,
                request,
                transport_protocol_version=(
                    protocol_version if protocol_version in LEGACY_PROTOCOL_VERSIONS else None
                ),
                principal=self._mcp_principal(),
            )
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
        self._json(rpc_response_status(request, response), response)

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
        try:
            client = _resolve_oauth_client(config, client_id)
        except ValueError as exc:
            return None, f"Invalid client metadata: {exc}"
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
        authorize_action = html.escape(
            f"{self._base_url()}/oauth/authorize",
            quote=True,
        )
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize Coding Tools MCP</title>
<style>body{{font-family:system-ui,sans-serif;max-width:520px;margin:60px auto;padding:0 20px}}input,button{{box-sizing:border-box;width:100%;padding:12px;margin:8px 0}}small{{color:#666}}</style></head>
<body><h1>Authorize Coding Tools MCP</h1><p>A client is requesting access to the configured workspace.</p>{error_html}
<form method="post" action="{authorize_action}">{hidden}<label>Password<input type="password" name="password" autocomplete="current-password" required></label><button type="submit">Authorize</button></form>
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
        normalized_resource = config.normalize_resource(requested_resource)
        if requested_resource and config.resource and normalized_resource != config.resource:
            self._html(
                400,
                self._authorize_page(
                    params,
                    "resource does not match this MCP server: "
                    f"expected {config.resource}, received {requested_resource}",
                ),
            )
            return
        code = config.issue_code(
            params["client_id"],
            params["redirect_uri"],
            params["code_challenge"],
            normalized_resource,
        )
        parsed = urllib.parse.urlparse(params["redirect_uri"])
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query.append(("code", code))
        if params.get("state"):
            query.append(("state", params["state"]))
        if config.issuer:
            query.append(("iss", config.issuer))
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
            try:
                client = _resolve_oauth_client(config, client_id)
            except ValueError:
                client = None
            auth_method = client.token_endpoint_auth_method if client else "none"
        if client_id != code.client_id:
            self._json(400, {"error": "invalid_grant"})
            return
        try:
            client = _resolve_oauth_client(config, client_id)
        except ValueError:
            client = None
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
        normalized_resource = config.normalize_resource(requested_resource)
        if requested_resource and code.resource and normalized_resource != code.resource:
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
            try:
                client = _resolve_oauth_client(config, client_id)
            except ValueError:
                client = None
            auth_method = client.token_endpoint_auth_method if client else "none"
        try:
            client = _resolve_oauth_client(config, client_id)
        except ValueError:
            client = None
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
        requested_resource = config.normalize_resource(params.get("resource", "").strip())
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
    config = OAuthConfig(
        password=password,
        server_url=server_url,
        token_secret=token_secret,
        token_ttl=token_ttl,
        refresh_token_ttl=refresh_token_ttl,
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

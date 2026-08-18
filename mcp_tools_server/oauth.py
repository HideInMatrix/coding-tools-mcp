"""OAuth 2.1 helpers used by the HTTP transport.

The implementation deliberately uses only the Python standard library.  Access
tokens are signed opaque server tokens rather than depending on a JWT package.
The public OAuthClient/OAuthClientRegistry API remains stable because the
desktop launcher persists dynamically registered clients through that API.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


OAUTH_TOKEN_TTL_SECONDS = 24 * 60 * 60
OAUTH_REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3600
OAUTH_CODE_TTL_SECONDS = 300
OAUTH_MAX_BODY_BYTES = 64 * 1024
MAX_PENDING_CODES = 256
MAX_REFRESH_TOKENS = 1024
MAX_CONSUMED_REFRESH_TOKENS = 2048


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _secret_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _redirect_uri_allowed(uri: str) -> bool:
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    if parsed.scheme == "https" and parsed.netloc:
        return True
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return True
    return False


def is_client_id_metadata_url(client_id: str) -> bool:
    """Return whether a client_id has the URL shape required by CIMD."""

    try:
        parsed = urlparse(client_id)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
        and parsed.path not in {"", "/"}
        and not parsed.fragment
    )


def client_from_metadata_document(
    client_id: str,
    metadata: dict[str, Any],
) -> "OAuthClient":
    """Validate a Client ID Metadata Document and build a public client."""

    if not is_client_id_metadata_url(client_id):
        raise ValueError("CIMD client_id must be an HTTPS URL with a path")
    if metadata.get("client_id") != client_id:
        raise ValueError("CIMD metadata client_id must exactly match its URL")
    client_name = metadata.get("client_name")
    if not isinstance(client_name, str) or not client_name.strip():
        raise ValueError("CIMD metadata requires client_name")
    raw_redirects = metadata.get("redirect_uris")
    if (
        not isinstance(raw_redirects, list)
        or not raw_redirects
        or not all(
            isinstance(uri, str) and _redirect_uri_allowed(uri)
            for uri in raw_redirects
        )
    ):
        raise ValueError("CIMD redirect_uris must contain valid OAuth callback URLs")
    grant_types = metadata.get("grant_types", ["authorization_code"])
    if not isinstance(grant_types, list) or "authorization_code" not in grant_types:
        raise ValueError("CIMD authorization_code grant is required")
    response_types = metadata.get("response_types", ["code"])
    if not isinstance(response_types, list) or "code" not in response_types:
        raise ValueError("CIMD code response type is required")
    method = metadata.get("token_endpoint_auth_method", "none")
    supported_methods = metadata.get("token_endpoint_auth_methods_supported", [])
    if not isinstance(supported_methods, list) or not all(
        isinstance(item, str) for item in supported_methods
    ):
        raise ValueError("CIMD token_endpoint_auth_methods_supported must be an array")
    # ChatGPT prefers private_key_jwt but publishes `none` as a supported
    # fallback. This authorization server advertises `none` (not
    # private_key_jwt), so negotiate the mutually supported public-client
    # method. PKCE remains mandatory for the authorization-code exchange.
    if method != "none":
        if "none" in supported_methods:
            method = "none"
        else:
            raise ValueError("CIMD client has no supported token endpoint auth method")
    application_type = metadata.get("application_type", "web")
    if application_type not in {"web", "native"}:
        raise ValueError("CIMD application_type must be web or native")
    return OAuthClient(
        client_id=client_id,
        redirect_uris=tuple(raw_redirects),
        token_endpoint_auth_method=method,
        client_name=client_name.strip(),
        secret_digest=None,
        issued_at=int(time.time()),
        application_type=application_type,
    )


@dataclass(frozen=True, slots=True)
class OAuthClient:
    client_id: str
    redirect_uris: tuple[str, ...]
    token_endpoint_auth_method: str
    client_name: str | None
    secret_digest: str | None
    issued_at: int
    application_type: str = "web"


class OAuthClientRegistry:
    """Thread-safe RFC 7591 client registry.

    Attribute names intentionally remain simple/private so
    ``coding_tools_launcher.oauth_persistence`` can serialize the registry.
    """

    def __init__(self) -> None:
        self._clients: dict[str, OAuthClient] = {}
        self._lock = threading.RLock()

    def get(self, client_id: str) -> OAuthClient | None:
        with self._lock:
            return self._clients.get(client_id)

    def list_clients(self) -> tuple[OAuthClient, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._clients.values(),
                    key=lambda client: (client.issued_at, client.client_id),
                )
            )

    def remove(self, client_id: str) -> bool:
        with self._lock:
            return self._clients.pop(client_id, None) is not None

    def clear(self) -> int:
        with self._lock:
            count = len(self._clients)
            self._clients.clear()
            return count

    def add_preregistered(
        self,
        client_id: str,
        redirect_uris: tuple[str, ...],
        *,
        client_secret: str | None,
    ) -> None:
        if not client_id:
            raise ValueError("client_id cannot be empty")
        if not redirect_uris or not all(_redirect_uri_allowed(uri) for uri in redirect_uris):
            raise ValueError("invalid OAuth redirect URI")
        method = "client_secret_post" if client_secret else "none"
        client = OAuthClient(
            client_id=client_id,
            redirect_uris=tuple(redirect_uris),
            token_endpoint_auth_method=method,
            client_name=None,
            secret_digest=_secret_digest(client_secret) if client_secret else None,
            issued_at=int(time.time()),
        )
        with self._lock:
            self._clients[client_id] = client

    def register(self, metadata: dict[str, Any]) -> dict[str, Any]:
        raw_redirects = metadata.get("redirect_uris")
        if not isinstance(raw_redirects, list) or not raw_redirects or not all(isinstance(uri, str) and _redirect_uri_allowed(uri) for uri in raw_redirects):
            raise ValueError("redirect_uris must contain valid OAuth callback URLs")
        method = metadata.get("token_endpoint_auth_method", "none")
        if method not in {"none", "client_secret_post", "client_secret_basic"}:
            raise ValueError("unsupported token_endpoint_auth_method")
        grant_types = metadata.get("grant_types", ["authorization_code"])
        if not isinstance(grant_types, list) or not all(isinstance(item, str) for item in grant_types):
            raise ValueError("grant_types must be an array of strings")
        if "authorization_code" not in grant_types:
            raise ValueError("authorization_code grant is required")
        unsupported_grants = set(grant_types) - {"authorization_code", "refresh_token"}
        if unsupported_grants:
            raise ValueError("unsupported grant_type")
        response_types = metadata.get("response_types", ["code"])
        if "code" not in response_types:
            raise ValueError("code response type is required")
        application_type = metadata.get("application_type", "web")
        if application_type not in {"web", "native"}:
            raise ValueError("application_type must be web or native")
        client_id = secrets.token_urlsafe(24)
        client_secret = secrets.token_urlsafe(32) if method != "none" else None
        client_name = metadata.get("client_name") if isinstance(metadata.get("client_name"), str) else None
        issued_at = int(time.time())
        client = OAuthClient(
            client_id,
            tuple(raw_redirects),
            method,
            client_name,
            _secret_digest(client_secret) if client_secret else None,
            issued_at,
            application_type,
        )
        with self._lock:
            self._clients[client_id] = client
        response: dict[str, Any] = {
            "client_id": client_id,
            "client_id_issued_at": issued_at,
            "redirect_uris": raw_redirects,
            "grant_types": grant_types,
            "response_types": ["code"],
            "token_endpoint_auth_method": method,
            "application_type": application_type,
        }
        if client_name:
            response["client_name"] = client_name
        if client_secret:
            response["client_secret"] = client_secret
            response["client_secret_expires_at"] = 0
        return response

    def authenticates(self, client_id: str, client_secret: str | None, method: str | None = None) -> bool:
        client = self.get(client_id)
        if client is None:
            return False
        actual_method = method or client.token_endpoint_auth_method
        if client.token_endpoint_auth_method == "none":
            return actual_method == "none"
        if actual_method not in {"client_secret_post", "client_secret_basic"} or client_secret is None or client.secret_digest is None:
            return False
        return hmac.compare_digest(client.secret_digest, _secret_digest(client_secret))


@dataclass(slots=True)
class AuthorizationCode:
    client_id: str
    redirect_uri: str
    challenge: str
    resource: str | None
    expires_at: int


@dataclass(slots=True)
class RefreshGrant:
    client_id: str
    resource: str | None
    expires_at: int


@dataclass(slots=True)
class OAuthConfig:
    password: str
    server_url: str | None
    token_secret: bytes
    token_ttl: int = OAUTH_TOKEN_TTL_SECONDS
    refresh_token_ttl: int = OAUTH_REFRESH_TOKEN_TTL_SECONDS
    registry: OAuthClientRegistry = field(default_factory=OAuthClientRegistry)
    cimd_cache: dict[str, tuple[OAuthClient, float]] = field(default_factory=dict)
    pending_codes: dict[str, AuthorizationCode] = field(default_factory=dict)
    consumed_refresh_tokens: dict[str, int] = field(default_factory=dict)
    refresh_tokens: dict[str, RefreshGrant] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def issuer(self) -> str | None:
        if not self.server_url:
            return None
        return self.server_url.rstrip("/")

    @property
    def resource(self) -> str | None:
        issuer = self.issuer
        if not issuer:
            return None
        return f"{issuer}/mcp"

    @property
    def legacy_resource(self) -> str | None:
        """Resource identifier used by releases before issuer/resource split."""

        return self.issuer

    def normalize_resource(self, resource: str | None) -> str | None:
        """Canonicalize resource values accepted from MCP OAuth clients.

        New tokens use the concrete ``/mcp`` endpoint as the RFC 8707/RFC 9728
        resource.  Releases before the issuer/resource split used the OAuth
        issuer/base URL as the resource, so accept that legacy alias during the
        compatibility window but always canonicalize it to ``/mcp``.
        """

        canonical = self.resource
        raw = str(resource or "").strip().rstrip("/")
        if not raw:
            return canonical
        if canonical is None:
            return raw
        if raw == canonical or raw == self.legacy_resource:
            return canonical
        return raw

    def issue_code(
        self,
        client_id: str,
        redirect_uri: str,
        challenge: str,
        resource: str | None = None,
    ) -> str:
        with self.lock:
            now = int(time.time())
            self.pending_codes = {code: item for code, item in self.pending_codes.items() if item.expires_at > now}
            while len(self.pending_codes) >= MAX_PENDING_CODES:
                self.pending_codes.pop(next(iter(self.pending_codes)))
            code = secrets.token_urlsafe(32)
            self.pending_codes[code] = AuthorizationCode(
                client_id,
                redirect_uri,
                challenge,
                self.normalize_resource(resource),
                now + OAUTH_CODE_TTL_SECONDS,
            )
            return code

    def consume_code(self, code: str) -> AuthorizationCode | None:
        with self.lock:
            item = self.pending_codes.pop(code, None)
        if item is None or item.expires_at <= int(time.time()):
            return None
        return item

    def issue_refresh_token(self, client_id: str, resource: str | None = None) -> str:
        now = int(time.time())
        payload = {
            "client_id": client_id,
            "resource": self.normalize_resource(resource),
            "iat": now,
            "exp": now + self.refresh_token_ttl,
            "jti": secrets.token_urlsafe(18),
        }
        encoded = _b64url(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = _b64url(
            hmac.new(self.token_secret, encoded.encode("ascii"), hashlib.sha256).digest()
        )
        return f"ctr1.{encoded}.{signature}"

    def consume_refresh_token(
        self,
        token: str,
        *,
        client_id: str,
        resource: str | None = None,
    ) -> RefreshGrant | None:
        if token.startswith("ctr1."):
            try:
                prefix, encoded, signature = token.split(".", 2)
                if prefix != "ctr1":
                    return None
                expected = _b64url(
                    hmac.new(
                        self.token_secret,
                        encoded.encode("ascii"),
                        hashlib.sha256,
                    ).digest()
                )
                if not hmac.compare_digest(signature, expected):
                    return None
                payload = json.loads(_b64url_decode(encoded))
                if not isinstance(payload, dict):
                    return None
                expires_at = int(payload.get("exp", 0))
                now = int(time.time())
                if expires_at <= now:
                    return None
                if payload.get("client_id") != client_id:
                    return None
                expected_resource = self.normalize_resource(resource)
                token_resource = self.normalize_resource(payload.get("resource"))
                if expected_resource and token_resource != expected_resource:
                    return None
                jti = payload.get("jti")
                if not isinstance(jti, str) or not jti:
                    return None
                digest = _secret_digest(jti)
                with self.lock:
                    self.consumed_refresh_tokens = {
                        key: expiry
                        for key, expiry in self.consumed_refresh_tokens.items()
                        if expiry > now
                    }
                    if digest in self.consumed_refresh_tokens:
                        return None
                    while len(self.consumed_refresh_tokens) >= MAX_CONSUMED_REFRESH_TOKENS:
                        self.consumed_refresh_tokens.pop(
                            next(iter(self.consumed_refresh_tokens))
                        )
                    self.consumed_refresh_tokens[digest] = expires_at
                return RefreshGrant(client_id, token_resource, expires_at)
            except (ValueError, TypeError, json.JSONDecodeError):
                return None

        # Compatibility path for refresh tokens issued by pre-migration
        # processes. These old opaque grants only survive while their original
        # process remains alive; all newly issued tokens use ctr1 above.
        digest = _secret_digest(token)
        with self.lock:
            grant = self.refresh_tokens.get(digest)
            if grant is None or grant.expires_at <= int(time.time()):
                if grant is not None:
                    self.refresh_tokens.pop(digest, None)
                return None
            expected_resource = self.normalize_resource(resource)
            if grant.client_id != client_id:
                return None
            if expected_resource and grant.resource and grant.resource != expected_resource:
                return None
            # Rotation is single-use: only consume after the client/resource
            # binding has been validated, so an invalid request cannot revoke
            # another client's legitimate refresh token.
            self.refresh_tokens.pop(digest, None)
            return grant


def valid_pkce_challenge(value: str) -> bool:
    if len(value) != 43:
        return False
    return all(char.isalnum() or char in "-_" for char in value)


def verify_pkce(verifier: str, challenge: str) -> bool:
    if not valid_pkce_challenge(verifier):
        return False
    expected = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return hmac.compare_digest(expected, challenge)


def create_access_token(config: OAuthConfig, client_id: str) -> str:
    now = int(time.time())
    payload_value: dict[str, Any] = {
        "sub": client_id,
        "client_id": client_id,
        "iat": now,
        "exp": now + config.token_ttl,
        "scope": "mcp",
    }
    if config.issuer:
        payload_value["iss"] = config.issuer
    if config.resource:
        payload_value["aud"] = config.resource
    payload = json.dumps(payload_value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = _b64url(payload)
    signature = _b64url(hmac.new(config.token_secret, encoded.encode("ascii"), hashlib.sha256).digest())
    return f"ctm1.{encoded}.{signature}"


def validate_access_token(config: OAuthConfig, token: str) -> bool:
    try:
        prefix, encoded, signature = token.split(".", 2)
        if prefix != "ctm1":
            return False
        expected = _b64url(hmac.new(config.token_secret, encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return False
        payload = json.loads(_b64url_decode(encoded))
        if not isinstance(payload, dict) or not isinstance(payload.get("sub"), str):
            return False
        if int(payload.get("exp", 0)) <= int(time.time()):
            return False
        client_id = payload.get("client_id", payload.get("sub"))
        if not isinstance(client_id, str):
            return False
        # DCR/preregistered clients remain revocable through the persisted
        # registry. CIMD clients use their HTTPS metadata URL as client_id and
        # are resolved at authorization/token time rather than persisted.
        parsed_client_id = urlparse(client_id)
        is_cimd_client = (
            parsed_client_id.scheme == "https"
            and bool(parsed_client_id.netloc)
            and parsed_client_id.path not in {"", "/"}
        )
        if config.registry.get(client_id) is None and not is_cimd_client:
            return False
        issuer = payload.get("iss")
        if issuer is not None and config.issuer and issuer != config.issuer:
            return False
        audience = payload.get("aud")
        # Accept pre-migration access tokens whose audience was the issuer/base
        # URL until they expire. New tokens are bound to the concrete /mcp
        # resource.
        if audience is not None and config.resource:
            if audience not in {config.resource, config.legacy_resource}:
                return False
        return True
    except (ValueError, TypeError, json.JSONDecodeError):
        return False


def access_token_client_id(config: OAuthConfig, token: str) -> str | None:
    """Return the stable client_id for a valid project access token."""

    if not validate_access_token(config, token):
        return None
    try:
        prefix, encoded, _signature = token.split(".", 2)
        if prefix != "ctm1":
            return None
        payload = json.loads(_b64url_decode(encoded))
        if not isinstance(payload, dict):
            return None
        client_id = payload.get("client_id", payload.get("sub"))
        return client_id if isinstance(client_id, str) and client_id else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None

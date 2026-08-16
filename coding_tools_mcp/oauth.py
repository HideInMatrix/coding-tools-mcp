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


OAUTH_TOKEN_TTL_SECONDS = 3600
OAUTH_CODE_TTL_SECONDS = 300
OAUTH_MAX_BODY_BYTES = 64 * 1024
MAX_PENDING_CODES = 256


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
    # Native app custom schemes are allowed when they are syntactically valid
    # and cannot be confused with HTTP origins.
    return bool(parsed.scheme and parsed.scheme not in {"http", "https"} and not parsed.username and not parsed.password)


@dataclass(frozen=True, slots=True)
class OAuthClient:
    client_id: str
    redirect_uris: tuple[str, ...]
    token_endpoint_auth_method: str
    client_name: str | None
    secret_digest: str | None
    issued_at: int


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
        if grant_types != ["authorization_code"] and "authorization_code" not in grant_types:
            raise ValueError("authorization_code grant is required")
        response_types = metadata.get("response_types", ["code"])
        if "code" not in response_types:
            raise ValueError("code response type is required")
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
        )
        with self._lock:
            self._clients[client_id] = client
        response: dict[str, Any] = {
            "client_id": client_id,
            "client_id_issued_at": issued_at,
            "redirect_uris": raw_redirects,
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": method,
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
    expires_at: int


@dataclass(slots=True)
class OAuthConfig:
    password: str
    server_url: str | None
    token_secret: bytes
    token_ttl: int = OAUTH_TOKEN_TTL_SECONDS
    registry: OAuthClientRegistry = field(default_factory=OAuthClientRegistry)
    pending_codes: dict[str, AuthorizationCode] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def issue_code(self, client_id: str, redirect_uri: str, challenge: str) -> str:
        with self.lock:
            now = int(time.time())
            self.pending_codes = {code: item for code, item in self.pending_codes.items() if item.expires_at > now}
            while len(self.pending_codes) >= MAX_PENDING_CODES:
                self.pending_codes.pop(next(iter(self.pending_codes)))
            code = secrets.token_urlsafe(32)
            self.pending_codes[code] = AuthorizationCode(client_id, redirect_uri, challenge, now + OAUTH_CODE_TTL_SECONDS)
            return code

    def consume_code(self, code: str) -> AuthorizationCode | None:
        with self.lock:
            item = self.pending_codes.pop(code, None)
        if item is None or item.expires_at <= int(time.time()):
            return None
        return item


def valid_pkce_challenge(value: str) -> bool:
    if not 43 <= len(value) <= 128:
        return False
    return all(char.isalnum() or char in "-._~" for char in value)


def verify_pkce(verifier: str, challenge: str) -> bool:
    if not valid_pkce_challenge(verifier):
        return False
    expected = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return hmac.compare_digest(expected, challenge)


def create_access_token(config: OAuthConfig, client_id: str) -> str:
    now = int(time.time())
    payload = json.dumps({"sub": client_id, "iat": now, "exp": now + config.token_ttl}, separators=(",", ":"), sort_keys=True).encode("utf-8")
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
        return isinstance(payload, dict) and isinstance(payload.get("sub"), str) and int(payload.get("exp", 0)) > int(time.time())
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
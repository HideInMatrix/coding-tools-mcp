"""OAuth domain services and runtime state."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .oauth import (
    OAUTH_CODE_TTL_SECONDS,
    OAUTH_REFRESH_TOKEN_TTL_SECONDS,
    OAUTH_TOKEN_TTL_SECONDS,
    OAuthClientRegistry,
    OAuthObservedClientRegistry,
    _b64url,
    _b64url_decode,
    _secret_digest,
)


MAX_PENDING_CODES = 256
MAX_CONSUMED_REFRESH_TOKENS = 2048


@dataclass(frozen=True, slots=True)
class AuthorizationCode:
    client_id: str
    redirect_uri: str
    challenge: str
    resource: str | None
    expires_at: int


@dataclass(frozen=True, slots=True)
class RefreshGrant:
    client_id: str
    resource: str | None
    expires_at: int


@dataclass(frozen=True, slots=True)
class OAuthSettings:
    password: str
    server_url: str | None
    token_secret: bytes
    cimd_enabled: bool = True
    token_ttl: int = OAUTH_TOKEN_TTL_SECONDS
    refresh_token_ttl: int = OAUTH_REFRESH_TOKEN_TTL_SECONDS

    @property
    def issuer(self) -> str | None:
        if not self.server_url:
            return None
        return self.server_url.rstrip("/")

    @property
    def resource(self) -> str | None:
        issuer = self.issuer
        return f"{issuer}/mcp" if issuer else None

    def normalize_resource(self, resource: str | None) -> str | None:
        canonical = self.resource
        raw = str(resource or "").strip().rstrip("/")
        if not raw:
            return canonical
        if canonical is None:
            return raw
        return canonical if raw == canonical else raw


class AuthorizationCodeStore:
    """Thread-safe, bounded one-time authorization-code store."""

    def __init__(
        self,
        *,
        ttl_seconds: int = OAUTH_CODE_TTL_SECONDS,
        max_codes: int = MAX_PENDING_CODES,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_codes = max_codes
        self._codes: dict[str, AuthorizationCode] = {}
        self._lock = threading.RLock()

    def issue(
        self,
        client_id: str,
        redirect_uri: str,
        challenge: str,
        resource: str | None,
    ) -> str:
        now = int(time.time())
        with self._lock:
            self._codes = {
                code: item
                for code, item in self._codes.items()
                if item.expires_at > now
            }
            while len(self._codes) >= self._max_codes:
                self._codes.pop(next(iter(self._codes)))
            code = secrets.token_urlsafe(32)
            self._codes[code] = AuthorizationCode(
                client_id=client_id,
                redirect_uri=redirect_uri,
                challenge=challenge,
                resource=resource,
                expires_at=now + self._ttl_seconds,
            )
            return code

    def consume(self, code: str) -> AuthorizationCode | None:
        with self._lock:
            item = self._codes.pop(code, None)
        if item is None or item.expires_at <= int(time.time()):
            return None
        return item


class RefreshTokenReplayGuard:
    """Bounded replay policy for single-use refresh-token JTIs."""

    def __init__(self, max_entries: int = MAX_CONSUMED_REFRESH_TOKENS) -> None:
        self._max_entries = max_entries
        self._consumed: dict[str, int] = {}
        self._lock = threading.RLock()

    def consume(self, jti: str, expires_at: int, *, now: int | None = None) -> bool:
        current = int(time.time()) if now is None else now
        digest = _secret_digest(jti)
        with self._lock:
            self._consumed = {
                key: expiry
                for key, expiry in self._consumed.items()
                if expiry > current
            }
            if digest in self._consumed:
                return False
            while len(self._consumed) >= self._max_entries:
                self._consumed.pop(next(iter(self._consumed)))
            self._consumed[digest] = expires_at
        return True


class OAuthTokenService:
    """Issue and validate access/refresh tokens for one OAuth service."""

    def __init__(
        self,
        settings: OAuthSettings,
        registry: OAuthClientRegistry,
        observed_clients: OAuthObservedClientRegistry,
        replay_guard: RefreshTokenReplayGuard | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.observed_clients = observed_clients
        self.replay_guard = replay_guard or RefreshTokenReplayGuard()

    def _sign(self, prefix: str, payload: dict[str, Any]) -> str:
        encoded = _b64url(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = _b64url(
            hmac.new(
                self.settings.token_secret,
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        return f"{prefix}.{encoded}.{signature}"

    def _decode_signed(self, token: str, prefix: str) -> dict[str, Any] | None:
        try:
            actual_prefix, encoded, signature = token.split(".", 2)
            if actual_prefix != prefix:
                return None
            expected = _b64url(
                hmac.new(
                    self.settings.token_secret,
                    encoded.encode("ascii"),
                    hashlib.sha256,
                ).digest()
            )
            if not hmac.compare_digest(signature, expected):
                return None
            payload = json.loads(_b64url_decode(encoded))
            return payload if isinstance(payload, dict) else None
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def issue_access_token(self, client_id: str) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": client_id,
            "client_id": client_id,
            "iat": now,
            "exp": now + self.settings.token_ttl,
            "scope": "mcp",
        }
        if self.settings.issuer:
            payload["iss"] = self.settings.issuer
        if self.settings.resource:
            payload["aud"] = self.settings.resource
        return self._sign("ctm1", payload)

    def access_token_claims(self, token: str) -> dict[str, Any] | None:
        payload = self._decode_signed(token, "ctm1")
        if payload is None or not isinstance(payload.get("sub"), str):
            return None
        if int(payload.get("exp", 0)) <= int(time.time()):
            return None
        client_id = payload.get("client_id", payload.get("sub"))
        if not isinstance(client_id, str):
            return None
        parsed_client_id = urlparse(client_id)
        is_cimd_client = (
            parsed_client_id.scheme == "https"
            and bool(parsed_client_id.netloc)
            and parsed_client_id.path not in {"", "/"}
        )
        if self.registry.get(client_id) is None and not is_cimd_client:
            return None
        issuer = payload.get("iss")
        if (
            issuer is not None
            and self.settings.issuer
            and issuer != self.settings.issuer
        ):
            return None
        audience = payload.get("aud")
        if (
            audience is not None
            and self.settings.resource
            and audience != self.settings.resource
        ):
            return None
        if is_cimd_client:
            self.observed_clients.observe_client_id(client_id)
        return payload

    def validate_access_token(self, token: str) -> bool:
        return self.access_token_claims(token) is not None

    def access_token_client_id(self, token: str) -> str | None:
        payload = self.access_token_claims(token)
        if payload is None:
            return None
        client_id = payload.get("client_id", payload.get("sub"))
        return client_id if isinstance(client_id, str) and client_id else None

    def issue_refresh_token(
        self,
        client_id: str,
        resource: str | None = None,
    ) -> str:
        now = int(time.time())
        return self._sign(
            "ctr1",
            {
                "client_id": client_id,
                "resource": self.settings.normalize_resource(resource),
                "iat": now,
                "exp": now + self.settings.refresh_token_ttl,
                "jti": secrets.token_urlsafe(18),
            },
        )

    def consume_refresh_token(
        self,
        token: str,
        *,
        client_id: str,
        resource: str | None = None,
    ) -> RefreshGrant | None:
        payload = self._decode_signed(token, "ctr1")
        if payload is None:
            return None
        try:
            expires_at = int(payload.get("exp", 0))
        except (TypeError, ValueError):
            return None
        now = int(time.time())
        if expires_at <= now or payload.get("client_id") != client_id:
            return None
        expected_resource = self.settings.normalize_resource(resource)
        token_resource = self.settings.normalize_resource(payload.get("resource"))
        if expected_resource and token_resource != expected_resource:
            return None
        jti = payload.get("jti")
        if not isinstance(jti, str) or not jti:
            return None
        if not self.replay_guard.consume(jti, expires_at, now=now):
            return None
        return RefreshGrant(client_id, token_resource, expires_at)


class OAuthService:
    """Composition root for OAuth settings, registries, codes and token policy."""

    def __init__(
        self,
        *,
        password: str,
        server_url: str | None,
        token_secret: bytes,
        cimd_enabled: bool = True,
        token_ttl: int = OAUTH_TOKEN_TTL_SECONDS,
        refresh_token_ttl: int = OAUTH_REFRESH_TOKEN_TTL_SECONDS,
        registry: OAuthClientRegistry | None = None,
        observed_clients: OAuthObservedClientRegistry | None = None,
    ) -> None:
        self.settings = OAuthSettings(
            password=password,
            server_url=server_url,
            token_secret=token_secret,
            cimd_enabled=cimd_enabled,
            token_ttl=token_ttl,
            refresh_token_ttl=refresh_token_ttl,
        )
        self.registry = registry or OAuthClientRegistry()
        self.observed_clients = observed_clients or OAuthObservedClientRegistry()
        self.codes = AuthorizationCodeStore()
        self.tokens = OAuthTokenService(
            self.settings,
            self.registry,
            self.observed_clients,
        )

    @property
    def password(self) -> str:
        return self.settings.password

    @property
    def server_url(self) -> str | None:
        return self.settings.server_url

    @property
    def token_secret(self) -> bytes:
        return self.settings.token_secret

    @property
    def cimd_enabled(self) -> bool:
        return self.settings.cimd_enabled

    @property
    def token_ttl(self) -> int:
        return self.settings.token_ttl

    @property
    def refresh_token_ttl(self) -> int:
        return self.settings.refresh_token_ttl

    @property
    def issuer(self) -> str | None:
        return self.settings.issuer

    @property
    def resource(self) -> str | None:
        return self.settings.resource

    def normalize_resource(self, resource: str | None) -> str | None:
        return self.settings.normalize_resource(resource)

    def issue_code(
        self,
        client_id: str,
        redirect_uri: str,
        challenge: str,
        resource: str | None = None,
    ) -> str:
        return self.codes.issue(
            client_id,
            redirect_uri,
            challenge,
            self.normalize_resource(resource),
        )

    def consume_code(self, code: str) -> AuthorizationCode | None:
        return self.codes.consume(code)

    def issue_refresh_token(
        self,
        client_id: str,
        resource: str | None = None,
    ) -> str:
        return self.tokens.issue_refresh_token(client_id, resource)

    def consume_refresh_token(
        self,
        token: str,
        *,
        client_id: str,
        resource: str | None = None,
    ) -> RefreshGrant | None:
        return self.tokens.consume_refresh_token(
            token,
            client_id=client_id,
            resource=resource,
        )


def create_access_token(service: OAuthService, client_id: str) -> str:
    return service.tokens.issue_access_token(client_id)


def validate_access_token(service: OAuthService, token: str) -> bool:
    return service.tokens.validate_access_token(token)


def access_token_client_id(service: OAuthService, token: str) -> str | None:
    return service.tokens.access_token_client_id(token)


__all__ = [
    "AuthorizationCode",
    "AuthorizationCodeStore",
    "OAuthService",
    "OAuthSettings",
    "OAuthTokenService",
    "RefreshGrant",
    "RefreshTokenReplayGuard",
    "access_token_client_id",
    "create_access_token",
    "validate_access_token",
]

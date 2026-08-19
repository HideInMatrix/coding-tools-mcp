"""Serializable Local MCP Gateway runtime configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..local_permission_broker import LocalPermissionBrokerClient
from ..oauth import OAuthClientRegistry, OAuthConfig
from ..runtime import Runtime
from .models import GatewayProfile
from .registry import GatewayProfileRegistry
from .runtime_pool import GatewayRuntimePool


GATEWAY_CONFIG_VERSION = 1


@dataclass(frozen=True, slots=True)
class GatewayOAuthSettings:
    password: str
    server_url: str
    token_secret_hex: str
    registry_file: Path

    def validated(self, profile: GatewayProfile) -> "GatewayOAuthSettings":
        password = self.password.strip()
        if not password:
            raise ValueError(f"gateway profile {profile.profile_id} OAuth password is required")
        server_url = self.server_url.strip().rstrip("/")
        parsed = urlsplit(server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                f"gateway profile {profile.profile_id} OAuth server_url must be http/https"
            )
        issuer_path = (parsed.path or "").rstrip("/")
        if issuer_path != profile.instance_path:
            raise ValueError(
                f"gateway profile {profile.profile_id} issuer path {issuer_path or '/'} "
                f"does not match instance_path {profile.instance_path}"
            )
        try:
            token_secret = bytes.fromhex(self.token_secret_hex.strip())
        except ValueError as exc:
            raise ValueError(
                f"gateway profile {profile.profile_id} OAuth token secret must be hex encoded"
            ) from exc
        if len(token_secret) < 32:
            raise ValueError(
                f"gateway profile {profile.profile_id} OAuth token secret must contain at least 32 bytes"
            )
        registry_file = self.registry_file.expanduser()
        if str(registry_file).strip() in {"", "."}:
            raise ValueError(
                f"gateway profile {profile.profile_id} OAuth registry_file is required"
            )
        if registry_file.exists() and registry_file.is_dir():
            raise ValueError(
                f"gateway profile {profile.profile_id} OAuth registry_file must be a file path"
            )
        return GatewayOAuthSettings(
            password=password,
            server_url=server_url,
            token_secret_hex=token_secret.hex(),
            registry_file=registry_file,
        )


@dataclass(frozen=True, slots=True)
class GatewayBrokerSettings:
    directory: Path
    secret_hex: str
    server_id: str

    def client(self) -> LocalPermissionBrokerClient:
        return LocalPermissionBrokerClient.from_values(
            directory=self.directory,
            secret_hex=self.secret_hex,
            server_id=self.server_id,
        )


@dataclass(frozen=True, slots=True)
class GatewayProfileConfig:
    profile: GatewayProfile
    oauth: GatewayOAuthSettings
    permission_broker: GatewayBrokerSettings | None = None

    def validated(self) -> "GatewayProfileConfig":
        profile = self.profile.validated()
        workspace = profile.workspace.resolve()
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError(
                f"gateway profile {profile.profile_id} workspace is not a directory: {workspace}"
            )
        profile = GatewayProfile(
            profile_id=profile.profile_id,
            instance_path=profile.instance_path,
            workspace=workspace,
            permission_mode=profile.permission_mode,
            allow_network=profile.allow_network,
            enable_view_image=profile.enable_view_image,
        )
        broker = self.permission_broker
        if broker is not None:
            # Validate eagerly; the client is recreated by the Runtime factory.
            broker.client()
        return GatewayProfileConfig(
            profile,
            self.oauth.validated(profile),
            broker,
        )


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    profiles: tuple[GatewayProfileConfig, ...]

    def validated(self) -> "GatewayConfig":
        validated = tuple(item.validated() for item in self.profiles)
        if not validated:
            raise ValueError("gateway config must contain at least one profile")
        registry = GatewayProfileRegistry()
        for item in validated:
            registry.register(item.profile)
        return GatewayConfig(validated)


def _profile_config_from_dict(value: dict[str, Any]) -> GatewayProfileConfig:
    oauth_raw = value.get("oauth")
    if not isinstance(oauth_raw, dict):
        raise ValueError("gateway profile oauth must be an object")
    profile = GatewayProfile(
        profile_id=str(value.get("profile_id") or ""),
        instance_path=str(value.get("instance_path") or ""),
        workspace=Path(str(value.get("workspace") or "")),
        permission_mode=str(value.get("permission_mode") or "safe"),
        allow_network=bool(value.get("allow_network", False)),
        enable_view_image=bool(value.get("enable_view_image", True)),
    )
    oauth = GatewayOAuthSettings(
        password=str(oauth_raw.get("password") or ""),
        server_url=str(oauth_raw.get("server_url") or ""),
        token_secret_hex=str(oauth_raw.get("token_secret_hex") or ""),
        registry_file=Path(str(oauth_raw.get("registry_file") or "")),
    )
    broker_raw = value.get("permission_broker")
    broker = None
    if broker_raw is not None:
        if not isinstance(broker_raw, dict):
            raise ValueError("gateway profile permission_broker must be an object")
        broker = GatewayBrokerSettings(
            directory=Path(str(broker_raw.get("directory") or "")),
            secret_hex=str(broker_raw.get("secret_hex") or ""),
            server_id=str(broker_raw.get("server_id") or profile.profile_id),
        )
    return GatewayProfileConfig(profile, oauth, broker)


def load_gateway_config(path: str | Path) -> GatewayConfig:
    config_path = Path(path).expanduser()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read gateway config: {config_path}") from exc
    if not isinstance(payload, dict) or payload.get("version") != GATEWAY_CONFIG_VERSION:
        raise ValueError("unsupported gateway config version")
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list):
        raise ValueError("gateway config profiles must be an array")
    profiles = []
    for raw in raw_profiles:
        if not isinstance(raw, dict):
            raise ValueError("gateway config profile must be an object")
        profiles.append(_profile_config_from_dict(raw))
    return GatewayConfig(tuple(profiles)).validated()


def build_gateway_runtime_pool(
    config: GatewayConfig,
) -> tuple[GatewayProfileRegistry, GatewayRuntimePool]:
    validated = config.validated()
    registry = GatewayProfileRegistry()
    settings: dict[str, GatewayOAuthSettings] = {}
    broker_settings: dict[str, GatewayBrokerSettings | None] = {}
    for item in validated.profiles:
        profile = registry.register(item.profile)
        settings[profile.profile_id] = item.oauth
        broker_settings[profile.profile_id] = item.permission_broker

    def factory(profile: GatewayProfile) -> Runtime:
        oauth = settings[profile.profile_id]
        broker = broker_settings[profile.profile_id]
        return Runtime(
            profile.workspace,
            permission_mode=profile.permission_mode,
            allow_network=profile.allow_network,
            oauth_config=OAuthConfig(
                password=oauth.password,
                server_url=oauth.server_url,
                token_secret=bytes.fromhex(oauth.token_secret_hex),
                registry=OAuthClientRegistry(oauth.registry_file),
            ),
            enable_view_image=profile.enable_view_image,
            permission_broker=broker.client() if broker is not None else None,
            permission_broker_from_env=False,
        )

    return registry, GatewayRuntimePool(registry, factory=factory)


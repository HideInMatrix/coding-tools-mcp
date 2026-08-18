from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DEFAULT_HOST, DEFAULT_PORT, LaunchConfig, NetworkConfig
from .user_settings import settings_dir


SERVER_PROFILE_SCHEMA_VERSION = 1
SERVER_LIFECYCLES = {"persistent", "ephemeral"}


def _timestamp() -> int:
    return int(time.time())


def default_lifecycle(network: NetworkConfig) -> str:
    """Return the default OAuth persistence lifecycle for a network config."""

    validated = network.validated()
    if validated.provider in {"cloudflare", "ngrok"} and not validated.public_url:
        return "ephemeral"
    return "persistent"


@dataclass(slots=True)
class MCPServerProfile:
    server_id: str
    name: str
    workspace: Path
    oauth_password: str
    network: NetworkConfig = field(default_factory=NetworkConfig)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    lifecycle: str = "persistent"
    created_at: int = field(default_factory=_timestamp)
    updated_at: int = field(default_factory=_timestamp)

    @classmethod
    def create(
        cls,
        *,
        name: str,
        workspace: Path,
        oauth_password: str,
        network: NetworkConfig | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        lifecycle: str | None = None,
    ) -> "MCPServerProfile":
        resolved_network = (network or NetworkConfig()).validated()
        now = _timestamp()
        return cls(
            server_id=uuid.uuid4().hex,
            name=name,
            workspace=workspace,
            oauth_password=oauth_password,
            network=resolved_network,
            host=host,
            port=port,
            lifecycle=lifecycle or default_lifecycle(resolved_network),
            created_at=now,
            updated_at=now,
        ).validated()

    def validated(self) -> "MCPServerProfile":
        server_id = self.server_id.strip()
        if not server_id:
            raise ValueError("server_id 不能为空。")

        name = self.name.strip()
        if not name:
            raise ValueError("服务名称不能为空。")

        host = self.host.strip() or DEFAULT_HOST
        if not 1 <= int(self.port) <= 65535:
            raise ValueError(f"无效端口: {self.port}")

        lifecycle = self.lifecycle.strip().lower()
        if lifecycle not in SERVER_LIFECYCLES:
            raise ValueError(f"不支持的 Server lifecycle: {lifecycle}")

        network = self.network.validated()
        workspace = self.workspace.expanduser()
        oauth_password = self.oauth_password.strip()

        return MCPServerProfile(
            server_id=server_id,
            name=name,
            workspace=workspace,
            oauth_password=oauth_password,
            network=network,
            host=host,
            port=int(self.port),
            lifecycle=lifecycle,
            created_at=int(self.created_at),
            updated_at=int(self.updated_at),
        )

    def to_launch_config(self) -> LaunchConfig:
        return LaunchConfig(
            workspace=self.workspace,
            oauth_password=self.oauth_password,
            network=self.network,
            host=self.host,
            port=self.port,
            server_id=self.server_id,
            lifecycle=self.lifecycle,
        ).validated()

    def to_dict(self) -> dict[str, Any]:
        profile = self.validated()
        return {
            "server_id": profile.server_id,
            "name": profile.name,
            "workspace": str(profile.workspace),
            "oauth_password": profile.oauth_password,
            "host": profile.host,
            "port": profile.port,
            "lifecycle": profile.lifecycle,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
            "network": {
                "provider": profile.network.provider,
                "public_url": profile.network.public_url,
                "options": dict(profile.network.options),
            },
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MCPServerProfile":
        network_raw = raw.get("network")
        if not isinstance(network_raw, dict):
            network_raw = {}
        options_raw = network_raw.get("options")
        if not isinstance(options_raw, dict):
            options_raw = {}
        try:
            profile = cls(
                server_id=str(raw["server_id"]),
                name=str(raw["name"]),
                workspace=Path(str(raw["workspace"])),
                oauth_password=str(raw["oauth_password"]),
                host=str(raw.get("host", DEFAULT_HOST)),
                port=int(raw.get("port", DEFAULT_PORT)),
                lifecycle=str(raw.get("lifecycle", "persistent")),
                created_at=int(raw.get("created_at", _timestamp())),
                updated_at=int(raw.get("updated_at", _timestamp())),
                network=NetworkConfig(
                    provider=str(network_raw.get("provider", "cloudflare")),
                    public_url=str(network_raw.get("public_url", "")),
                    options={str(key): str(value) for key, value in options_raw.items()},
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Server Profile 数据格式无效。") from exc
        return profile.validated()


class ServerProfileStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (settings_dir() / "servers.json")

    def list(self) -> list[MCPServerProfile]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Server Profile 文件损坏: {self.path}") from exc
        if not isinstance(payload, dict) or payload.get("version") != SERVER_PROFILE_SCHEMA_VERSION:
            raise RuntimeError(f"Server Profile 文件格式不受支持: {self.path}")
        raw_profiles = payload.get("servers")
        if not isinstance(raw_profiles, list):
            raise RuntimeError(f"Server Profile servers 字段无效: {self.path}")
        profiles: list[MCPServerProfile] = []
        try:
            for item in raw_profiles:
                if not isinstance(item, dict):
                    raise ValueError("profile entry must be an object")
                profiles.append(MCPServerProfile.from_dict(item))
        except ValueError as exc:
            raise RuntimeError(f"Server Profile 内容无效: {self.path}") from exc
        return profiles

    def get(self, server_id: str) -> MCPServerProfile | None:
        target = server_id.strip()
        for profile in self.list():
            if profile.server_id == target:
                return profile
        return None

    def next_default_port(self, start: int = DEFAULT_PORT) -> int:
        used = {profile.port for profile in self.list()}
        for port in range(max(1, int(start)), 65536):
            if port not in used:
                return port
        raise RuntimeError("没有可用的 TCP 端口可分配。")

    def create(
        self,
        *,
        name: str,
        workspace: Path,
        oauth_password: str,
        network: NetworkConfig | None = None,
        host: str = DEFAULT_HOST,
        port: int | None = None,
        lifecycle: str | None = None,
    ) -> MCPServerProfile:
        selected_port = self.next_default_port() if port is None else int(port)
        profile = MCPServerProfile.create(
            name=name,
            workspace=workspace,
            oauth_password=oauth_password,
            network=network,
            host=host,
            port=selected_port,
            lifecycle=lifecycle,
        )
        profiles = self.list()
        profiles.append(profile)
        self._save(profiles)
        return profile

    def save(self, profile: MCPServerProfile) -> MCPServerProfile:
        validated = profile.validated()
        profiles = self.list()
        now = _timestamp()
        replacement = MCPServerProfile(
            server_id=validated.server_id,
            name=validated.name,
            workspace=validated.workspace,
            oauth_password=validated.oauth_password,
            network=validated.network,
            host=validated.host,
            port=validated.port,
            lifecycle=validated.lifecycle,
            created_at=validated.created_at,
            updated_at=now,
        )
        for index, existing in enumerate(profiles):
            if existing.server_id == replacement.server_id:
                profiles[index] = replacement
                self._save(profiles)
                return replacement
        profiles.append(replacement)
        self._save(profiles)
        return replacement

    def delete(self, server_id: str) -> bool:
        target = server_id.strip()
        profiles = self.list()
        remaining = [profile for profile in profiles if profile.server_id != target]
        if len(remaining) == len(profiles):
            return False
        self._save(remaining)
        return True

    def _save(self, profiles: list[MCPServerProfile]) -> None:
        ids: set[str] = set()
        endpoints: set[tuple[str, int]] = set()
        validated_profiles: list[MCPServerProfile] = []
        for profile in profiles:
            validated = profile.validated()
            if validated.server_id in ids:
                raise ValueError(f"重复 server_id: {validated.server_id}")
            ids.add(validated.server_id)
            endpoint = (validated.host, validated.port)
            if endpoint in endpoints:
                raise ValueError(
                    f"多个 Server Profile 不能配置相同地址: {validated.host}:{validated.port}"
                )
            endpoints.add(endpoint)
            validated_profiles.append(validated)

        payload = {
            "version": SERVER_PROFILE_SCHEMA_VERSION,
            "servers": [profile.to_dict() for profile in validated_profiles],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            if os.name != "nt":
                temporary_path.chmod(0o600)
            os.replace(temporary_path, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
        finally:
            if temporary_path.exists():
                temporary_path.unlink(missing_ok=True)

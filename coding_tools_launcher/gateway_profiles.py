from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from mcp_tools_server.gateway import normalize_instance_path

from .config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    PERMISSION_MODE_CHOICES,
    NetworkConfig,
)
from .gateway_launcher import GatewayLaunchConfig
from .gateway_process import GatewayChildProfile
from .user_settings import settings_dir


GATEWAY_PROFILE_SCHEMA_VERSION = 1
GATEWAY_MEMBER_LIFECYCLES = {"persistent", "ephemeral"}


def _timestamp() -> int:
    return int(time.time())


def _public_hostname(value: str) -> str:
    raw = value.strip().rstrip("/")
    if not raw:
        return ""
    return (urlsplit(raw).hostname or "").lower()


@dataclass(frozen=True, slots=True)
class MCPGatewayMember:
    server_id: str
    name: str
    workspace: Path
    oauth_password: str
    instance_path: str
    permission_mode: str = "safe"
    lifecycle: str = "persistent"
    allow_network: bool = False
    enable_view_image: bool = True

    @classmethod
    def create(
        cls,
        *,
        name: str,
        workspace: Path,
        oauth_password: str,
        instance_path: str,
        permission_mode: str = "safe",
        lifecycle: str = "persistent",
        allow_network: bool = False,
        enable_view_image: bool = True,
    ) -> "MCPGatewayMember":
        return cls(
            server_id=uuid.uuid4().hex,
            name=name,
            workspace=workspace,
            oauth_password=oauth_password,
            instance_path=instance_path,
            permission_mode=permission_mode,
            lifecycle=lifecycle,
            allow_network=allow_network,
            enable_view_image=enable_view_image,
        ).validated()

    def validated(self) -> "MCPGatewayMember":
        server_id = self.server_id.strip()
        if not server_id:
            raise ValueError("Gateway Member server_id 不能为空。")
        name = self.name.strip()
        if not name:
            raise ValueError("Gateway Member 名称不能为空。")
        workspace = self.workspace.expanduser()
        password = self.oauth_password.strip()
        permission_mode = self.permission_mode.strip().lower() or "safe"
        if permission_mode not in PERMISSION_MODE_CHOICES:
            raise ValueError(f"不支持的权限模式: {permission_mode}")
        lifecycle = self.lifecycle.strip().lower() or "persistent"
        if lifecycle not in GATEWAY_MEMBER_LIFECYCLES:
            raise ValueError(f"不支持的 Gateway Member lifecycle: {lifecycle}")
        return MCPGatewayMember(
            server_id=server_id,
            name=name,
            workspace=workspace,
            oauth_password=password,
            instance_path=normalize_instance_path(self.instance_path),
            permission_mode=permission_mode,
            lifecycle=lifecycle,
            allow_network=bool(self.allow_network),
            enable_view_image=bool(self.enable_view_image),
        )

    def to_child_profile(self) -> GatewayChildProfile:
        value = self.validated()
        return GatewayChildProfile(
            server_id=value.server_id,
            name=value.name,
            workspace=value.workspace,
            oauth_password=value.oauth_password,
            instance_path=value.instance_path,
            permission_mode=value.permission_mode,
            lifecycle=value.lifecycle,
            allow_network=value.allow_network,
            enable_view_image=value.enable_view_image,
        )

    def to_dict(self) -> dict[str, object]:
        value = self.validated()
        return {
            "server_id": value.server_id,
            "name": value.name,
            "workspace": str(value.workspace),
            "oauth_password": value.oauth_password,
            "instance_path": value.instance_path,
            "permission_mode": value.permission_mode,
            "lifecycle": value.lifecycle,
            "allow_network": value.allow_network,
            "enable_view_image": value.enable_view_image,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MCPGatewayMember":
        return cls(
            server_id=str(value.get("server_id") or ""),
            name=str(value.get("name") or ""),
            workspace=Path(str(value.get("workspace") or "")),
            oauth_password=str(value.get("oauth_password") or ""),
            instance_path=str(value.get("instance_path") or ""),
            permission_mode=str(value.get("permission_mode") or "safe"),
            lifecycle=str(value.get("lifecycle") or "persistent"),
            allow_network=bool(value.get("allow_network", False)),
            enable_view_image=bool(value.get("enable_view_image", True)),
        ).validated()


@dataclass(frozen=True, slots=True)
class MCPGatewayProfile:
    gateway_id: str
    name: str
    network: NetworkConfig
    members: tuple[MCPGatewayMember, ...]
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    created_at: int = field(default_factory=_timestamp)
    updated_at: int = field(default_factory=_timestamp)

    @classmethod
    def create(
        cls,
        *,
        name: str,
        network: NetworkConfig,
        members: tuple[MCPGatewayMember, ...],
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> "MCPGatewayProfile":
        now = _timestamp()
        return cls(
            gateway_id=uuid.uuid4().hex,
            name=name,
            network=network,
            members=members,
            host=host,
            port=port,
            created_at=now,
            updated_at=now,
        ).validated()

    def validated(self) -> "MCPGatewayProfile":
        gateway_id = self.gateway_id.strip()
        if not gateway_id:
            raise ValueError("gateway_id 不能为空。")
        name = self.name.strip()
        if not name:
            raise ValueError("Gateway 名称不能为空。")
        network = self.network.validated()
        members = tuple(member.validated() for member in self.members)
        if not members:
            raise ValueError("Gateway 至少需要一个 Member。")
        host = self.host.strip() or DEFAULT_HOST
        port = int(self.port)
        if not 1 <= port <= 65535:
            raise ValueError(f"无效 Gateway 端口: {port}")
        if network.public_url:
            parsed = urlsplit(network.public_url)
            if (parsed.path or "").rstrip("/"):
                raise ValueError(
                    "Gateway 固定 Public URL 只能包含 hostname；Member Path 单独配置。"
                )
        member_ids: set[str] = set()
        member_paths: set[str] = set()
        for member in members:
            if member.server_id in member_ids:
                raise ValueError(f"重复 Gateway Member server_id: {member.server_id}")
            if member.instance_path in member_paths:
                raise ValueError(f"重复 Gateway Member Path: {member.instance_path}")
            member_ids.add(member.server_id)
            member_paths.add(member.instance_path)
        return MCPGatewayProfile(
            gateway_id=gateway_id,
            name=name,
            network=network,
            members=members,
            host=host,
            port=port,
            created_at=int(self.created_at),
            updated_at=int(self.updated_at),
        )

    def to_launch_config(self) -> GatewayLaunchConfig:
        value = self.validated()
        return GatewayLaunchConfig(
            network=value.network,
            profiles=tuple(member.to_child_profile() for member in value.members),
            host=value.host,
            port=value.port,
        ).validated()

    def to_dict(self) -> dict[str, object]:
        value = self.validated()
        return {
            "gateway_id": value.gateway_id,
            "name": value.name,
            "host": value.host,
            "port": value.port,
            "created_at": value.created_at,
            "updated_at": value.updated_at,
            "network": {
                "provider": value.network.provider,
                "public_url": value.network.public_url,
                "options": dict(value.network.options),
            },
            "members": [member.to_dict() for member in value.members],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MCPGatewayProfile":
        network_raw = value.get("network")
        if not isinstance(network_raw, dict):
            raise ValueError("Gateway network 字段无效。")
        options_raw = network_raw.get("options")
        options = options_raw if isinstance(options_raw, dict) else {}
        members_raw = value.get("members")
        if not isinstance(members_raw, list):
            raise ValueError("Gateway members 字段无效。")
        return cls(
            gateway_id=str(value.get("gateway_id") or ""),
            name=str(value.get("name") or ""),
            network=NetworkConfig(
                provider=str(network_raw.get("provider") or "cloudflare"),
                public_url=str(network_raw.get("public_url") or ""),
                options={str(key): str(item) for key, item in options.items()},
            ),
            members=tuple(
                MCPGatewayMember.from_dict(item)
                for item in members_raw
                if isinstance(item, dict)
            ),
            host=str(value.get("host") or DEFAULT_HOST),
            port=int(value.get("port") or DEFAULT_PORT),
            created_at=int(value.get("created_at") or _timestamp()),
            updated_at=int(value.get("updated_at") or _timestamp()),
        ).validated()


class GatewayProfileStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (settings_dir() / "gateways.json")

    def list(self) -> list[MCPGatewayProfile]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Gateway Profile 文件损坏: {self.path}") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != GATEWAY_PROFILE_SCHEMA_VERSION
        ):
            raise RuntimeError(f"Gateway Profile 文件格式不受支持: {self.path}")
        raw_gateways = payload.get("gateways")
        if not isinstance(raw_gateways, list):
            raise RuntimeError(f"Gateway Profile gateways 字段无效: {self.path}")
        try:
            return [
                MCPGatewayProfile.from_dict(item)
                for item in raw_gateways
                if isinstance(item, dict)
            ]
        except ValueError as exc:
            raise RuntimeError(f"Gateway Profile 内容无效: {self.path}") from exc

    def get(self, gateway_id: str) -> MCPGatewayProfile | None:
        target = gateway_id.strip()
        return next(
            (gateway for gateway in self.list() if gateway.gateway_id == target),
            None,
        )

    def create(
        self,
        *,
        name: str,
        network: NetworkConfig,
        members: tuple[MCPGatewayMember, ...],
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> MCPGatewayProfile:
        gateway = MCPGatewayProfile.create(
            name=name,
            network=network,
            members=members,
            host=host,
            port=port,
        )
        gateways = self.list()
        gateways.append(gateway)
        self._save(gateways)
        return gateway

    def save(self, gateway: MCPGatewayProfile) -> MCPGatewayProfile:
        validated = gateway.validated()
        replacement = MCPGatewayProfile(
            gateway_id=validated.gateway_id,
            name=validated.name,
            network=validated.network,
            members=validated.members,
            host=validated.host,
            port=validated.port,
            created_at=validated.created_at,
            updated_at=_timestamp(),
        )
        gateways = self.list()
        for index, existing in enumerate(gateways):
            if existing.gateway_id == replacement.gateway_id:
                gateways[index] = replacement
                self._save(gateways)
                return replacement
        gateways.append(replacement)
        self._save(gateways)
        return replacement

    def delete(self, gateway_id: str) -> bool:
        target = gateway_id.strip()
        gateways = self.list()
        remaining = [item for item in gateways if item.gateway_id != target]
        if len(remaining) == len(gateways):
            return False
        self._save(remaining)
        return True

    def next_default_port(self, start: int = DEFAULT_PORT) -> int:
        used = {gateway.port for gateway in self.list()}
        for port in range(max(1, int(start)), 65536):
            if port not in used:
                return port
        raise RuntimeError("没有可用的 Gateway TCP 端口可分配。")

    def _save(self, gateways: list[MCPGatewayProfile]) -> None:
        ids: set[str] = set()
        endpoints: set[tuple[str, int]] = set()
        hostnames: set[str] = set()
        member_ids: set[str] = set()
        validated: list[MCPGatewayProfile] = []
        for gateway in gateways:
            item = gateway.validated()
            if item.gateway_id in ids:
                raise ValueError(f"重复 gateway_id: {item.gateway_id}")
            ids.add(item.gateway_id)
            endpoint = (item.host, item.port)
            if endpoint in endpoints:
                raise ValueError(
                    f"多个 Gateway 不能配置相同地址: {item.host}:{item.port}"
                )
            endpoints.add(endpoint)
            hostname = _public_hostname(item.network.public_url)
            if hostname:
                if hostname in hostnames:
                    raise ValueError(
                        "多个 Gateway 不能配置相同 Public Hostname。"
                    )
                hostnames.add(hostname)
            for member in item.members:
                if member.server_id in member_ids:
                    raise ValueError(
                        f"Gateway Member server_id 必须全局唯一: {member.server_id}"
                    )
                member_ids.add(member.server_id)
            validated.append(item)

        payload = {
            "version": GATEWAY_PROFILE_SCHEMA_VERSION,
            "gateways": [item.to_dict() for item in validated],
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
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                temporary_path.chmod(0o600)
            os.replace(temporary_path, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
        finally:
            temporary_path.unlink(missing_ok=True)


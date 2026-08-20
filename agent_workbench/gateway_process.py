from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from agent_runtime.gateway import normalize_instance_path
from agent_runtime.local_permission_broker import (
    BROKER_DIR_ENV,
    BROKER_SECRET_ENV,
    BROKER_SERVER_ID_ENV,
)

from .config import DEFAULT_HOST, DEFAULT_PORT, PERMISSION_MODE_CHOICES
from .mcp_process import INTERNAL_MCP_FLAG
from .oauth_persistence import (
    OAuthPersistence,
    bind_server_oauth_issuer,
    canonical_oauth_issuer,
    migrate_oauth_storage_to_issuer,
    prepare_ephemeral_oauth_persistence,
    prepare_issuer_oauth_persistence,
)
from .process_utils import (
    LogCallback,
    forward_process_output,
    hidden_process_kwargs,
    stop_process,
    wait_for_tcp_port,
)
from .resources import PROJECT_ROOT, is_frozen


@dataclass(frozen=True, slots=True)
class GatewayChildProfile:
    server_id: str
    name: str
    workspace: Path
    oauth_password: str
    instance_path: str
    permission_mode: str = "safe"
    lifecycle: str = "persistent"
    allow_network: bool = False
    enable_view_image: bool = True

    def validated(self) -> "GatewayChildProfile":
        server_id = self.server_id.strip()
        if not server_id:
            raise ValueError("Gateway Profile server_id 不能为空。")
        name = self.name.strip()
        if not name:
            raise ValueError("Gateway Profile 名称不能为空。")
        workspace = self.workspace.expanduser().resolve()
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError(f"Gateway Profile Workspace 无效: {workspace}")
        password = self.oauth_password.strip()
        if not password:
            raise ValueError("Gateway Profile OAuth 登录密码不能为空。")
        permission_mode = self.permission_mode.strip().lower() or "safe"
        if permission_mode not in PERMISSION_MODE_CHOICES:
            raise ValueError(f"不支持的权限模式: {permission_mode}")
        lifecycle = self.lifecycle.strip().lower() or "persistent"
        if lifecycle not in {"persistent", "ephemeral"}:
            raise ValueError(f"不支持的 Gateway Profile lifecycle: {lifecycle}")
        return GatewayChildProfile(
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


@dataclass(frozen=True, slots=True)
class GatewayProcessConfig:
    public_base_url: str
    profiles: tuple[GatewayChildProfile, ...]
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    def validated(self) -> "GatewayProcessConfig":
        public_base_url = canonical_oauth_issuer(self.public_base_url)
        parsed = urlsplit(public_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Gateway Public URL 必须是完整的 http/https URL。")
        if (parsed.path or "").rstrip("/"):
            raise ValueError(
                "Gateway Public URL 必须只包含 hostname；Profile Path 由子 Profile 单独配置。"
            )
        if not 1 <= int(self.port) <= 65535:
            raise ValueError(f"无效 Gateway 端口: {self.port}")
        profiles = tuple(profile.validated() for profile in self.profiles)
        if not profiles:
            raise ValueError("Gateway 至少需要一个 Profile。")
        ids: set[str] = set()
        paths: set[str] = set()
        for profile in profiles:
            if profile.server_id in ids:
                raise ValueError(f"重复 Gateway Profile server_id: {profile.server_id}")
            if profile.instance_path in paths:
                raise ValueError(f"重复 Gateway Profile Path: {profile.instance_path}")
            ids.add(profile.server_id)
            paths.add(profile.instance_path)
        return GatewayProcessConfig(
            public_base_url=public_base_url,
            profiles=profiles,
            host=self.host.strip() or DEFAULT_HOST,
            port=int(self.port),
        )


@dataclass(slots=True)
class PreparedGatewayConfig:
    config_file: Path
    temporary_dir: Path
    persistences: tuple[OAuthPersistence, ...]
    registry_files: dict[str, Path]

    def remove_config_file(self) -> None:
        try:
            self.config_file.unlink(missing_ok=True)
        except OSError:
            pass

    def cleanup(self) -> None:
        self.remove_config_file()
        for persistence in self.persistences:
            persistence.cleanup()
        shutil.rmtree(self.temporary_dir, ignore_errors=True)


def prepare_gateway_config(
    config: GatewayProcessConfig,
    *,
    permission_broker: object | None = None,
) -> PreparedGatewayConfig:
    validated = config.validated()
    directory = Path(tempfile.mkdtemp(prefix="micromatrix-workbench-gateway-"))
    try:
        if os.name != "nt":
            directory.chmod(0o700)
        profile_payloads: list[dict[str, object]] = []
        persistences: list[OAuthPersistence] = []
        registry_files: dict[str, Path] = {}
        for profile in validated.profiles:
            issuer = f"{validated.public_base_url}{profile.instance_path}"
            if profile.lifecycle == "ephemeral":
                persistence = prepare_ephemeral_oauth_persistence(profile.server_id)
            else:
                migrate_oauth_storage_to_issuer(issuer, server_id=profile.server_id)
                persistence = prepare_issuer_oauth_persistence(issuer)
                bind_server_oauth_issuer(profile.server_id, issuer)
            persistences.append(persistence)
            registry_files[profile.server_id] = persistence.registry_file
            profile_payload: dict[str, object] = {
                    "profile_id": profile.server_id,
                    "instance_path": profile.instance_path,
                    "workspace": str(profile.workspace),
                    "permission_mode": profile.permission_mode,
                    "allow_network": profile.allow_network,
                    "enable_view_image": profile.enable_view_image,
                    "oauth": {
                        "password": profile.oauth_password,
                        "server_url": issuer,
                        "token_secret_hex": persistence.token_secret_hex,
                        "registry_file": str(persistence.registry_file),
                        "cimd_enabled": profile.lifecycle != "ephemeral",
                    },
                }
            if permission_broker is not None:
                child_environment = getattr(permission_broker, "child_environment", None)
                if not callable(child_environment):
                    raise ValueError("permission_broker does not provide child_environment")
                broker_env = child_environment(profile.server_id)
                if not isinstance(broker_env, dict):
                    raise ValueError("permission_broker child_environment must return a dict")
                broker_directory = str(
                    broker_env.get(BROKER_DIR_ENV) or ""
                ).strip()
                secret = str(broker_env.get(BROKER_SECRET_ENV) or "").strip()
                server_id = str(
                    broker_env.get(BROKER_SERVER_ID_ENV) or profile.server_id
                ).strip()
                if broker_directory and secret:
                    profile_payload["permission_broker"] = {
                        "directory": broker_directory,
                        "secret_hex": secret,
                        "server_id": server_id,
                    }
            profile_payloads.append(profile_payload)
        config_file = directory / "gateway.json"
        config_file.write_text(
            json.dumps(
                {"version": 1, "profiles": profile_payloads},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        if os.name != "nt":
            config_file.chmod(0o600)
        return PreparedGatewayConfig(
            config_file=config_file,
            temporary_dir=directory,
            persistences=tuple(persistences),
            registry_files=registry_files,
        )
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def build_gateway_mcp_command(
    config: GatewayProcessConfig,
    gateway_config_file: Path,
) -> list[str]:
    validated = config.validated()
    arguments = [
        "--host",
        validated.host,
        "--port",
        str(validated.port),
        "--gateway-config",
        str(gateway_config_file),
    ]
    if is_frozen():
        return [sys.executable, INTERNAL_MCP_FLAG, *arguments]
    return [sys.executable, "-m", "agent_workbench.mcp_worker", *arguments]


class GatewayServerProcess:
    def __init__(self, log: LogCallback, *, permission_broker: object | None = None):
        self._log = log
        self._permission_broker = permission_broker
        self.process: subprocess.Popen[str] | None = None
        self._prepared: PreparedGatewayConfig | None = None

    def start(self, config: GatewayProcessConfig, env: dict[str, str]) -> None:
        validated = config.validated()
        prepared = prepare_gateway_config(
            validated,
            permission_broker=self._permission_broker,
        )
        self._prepared = prepared
        command = build_gateway_mcp_command(validated, prepared.config_file)
        self._log(
            f"启动 Local MCP Gateway，Profiles: {len(validated.profiles)}，"
            f"监听 {validated.host}:{validated.port}"
        )
        try:
            self.process = subprocess.Popen(
                command,
                env=env,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                **hidden_process_kwargs(),
            )
            forward_process_output(self.process, prefix="gateway", log=self._log)
            wait_for_tcp_port(
                validated.host,
                validated.port,
                process=self.process,
            )
            # The child loads the whole config before binding. Remove passwords
            # and token secrets from the temporary filesystem as soon as the
            # listening socket proves startup completed.
            prepared.remove_config_file()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        stop_process(self.process, name="agent-runtime-gateway", log=self._log)
        self.process = None
        prepared = self._prepared
        self._prepared = None
        if prepared is not None:
            prepared.cleanup()

    def oauth_registry_file(self, server_id: str) -> Path | None:
        prepared = self._prepared
        if prepared is None:
            return None
        return prepared.registry_files.get(server_id.strip())


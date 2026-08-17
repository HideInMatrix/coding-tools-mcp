from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8234
NETWORK_PROVIDER_CHOICES = (
    "cloudflare",
    "frp",
    "ngrok",
    "tailscale",
    "external",
)


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件: {path}")

    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number} 配置格式错误，应为 KEY=VALUE")

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"{path}:{line_number} KEY 不能为空")
        if len(value) >= 2 and (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            value = value[1:-1]
        result[key] = value
    return result


def normalize_server_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if value.endswith("/mcp"):
        value = value[:-4].rstrip("/")

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "固定 MCP 地址必须是完整的 http/https URL，例如 https://mcp.example.com"
        )
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


@dataclass(slots=True)
class NetworkConfig:
    provider: str = "cloudflare"
    public_url: str = ""
    options: dict[str, str] = field(default_factory=dict)

    def validated(self) -> "NetworkConfig":
        provider = self.provider.strip().lower() or "cloudflare"
        if provider not in NETWORK_PROVIDER_CHOICES:
            raise ValueError(f"不支持的网络提供方案: {provider}")
        return NetworkConfig(
            provider=provider,
            public_url=normalize_server_url(self.public_url),
            options={str(key): str(value).strip() for key, value in self.options.items()},
        )


@dataclass(slots=True)
class LaunchConfig:
    workspace: Path
    oauth_password: str
    network: NetworkConfig = field(default_factory=NetworkConfig)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    def validated(self) -> "LaunchConfig":
        workspace = self.workspace.expanduser().resolve()
        if not workspace.exists():
            raise ValueError(f"Workspace 不存在: {workspace}")
        if not workspace.is_dir():
            raise ValueError(f"Workspace 不是目录: {workspace}")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"无效端口: {self.port}")

        oauth_password = self.oauth_password.strip()
        if not oauth_password:
            raise ValueError("缺少 OAuth 登录密码。")

        network = self.network.validated()

        return LaunchConfig(
            workspace=workspace,
            oauth_password=oauth_password,
            network=network,
            host=self.host.strip() or DEFAULT_HOST,
            port=self.port,
        )

    @classmethod
    def from_env(
        cls,
        *,
        workspace: Path,
        env: dict[str, str],
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> "LaunchConfig":
        provider = env.get("CODING_TOOLS_MCP_NETWORK_PROVIDER", "cloudflare")
        provider_options = {
            "tunnel_token": env.get("CODING_TOOLS_MCP_TUNNEL_TOKEN", ""),
            "executable": "",
            "config_file": "",
            "authtoken": "",
        }
        if provider == "frp":
            provider_options.update(
                {
                    "executable": env.get("CODING_TOOLS_MCP_FRPC", ""),
                    "config_file": env.get("CODING_TOOLS_MCP_FRP_CONFIG", ""),
                }
            )
        elif provider == "ngrok":
            provider_options.update(
                {
                    "executable": env.get("CODING_TOOLS_MCP_NGROK", ""),
                    "authtoken": env.get("CODING_TOOLS_MCP_NGROK_AUTHTOKEN", ""),
                }
            )
        elif provider == "tailscale":
            provider_options["executable"] = env.get(
                "CODING_TOOLS_MCP_TAILSCALE",
                "",
            )

        return cls(
            workspace=workspace,
            oauth_password=env.get("CODING_TOOLS_MCP_OAUTH_PASSWORD", ""),
            network=NetworkConfig(
                provider=provider,
                public_url=env.get("CODING_TOOLS_MCP_SERVER_URL", ""),
                options=provider_options,
            ),
            host=host,
            port=port,
        ).validated()


@dataclass(frozen=True, slots=True)
class LaunchInfo:
    workspace: Path
    local_mcp_url: str
    tunnel_url: str
    public_base_url: str
    public_mcp_url: str
    url_mode: str

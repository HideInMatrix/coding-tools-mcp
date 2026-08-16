from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8234


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
class LaunchConfig:
    workspace: Path
    oauth_client_id: str
    oauth_client_secret: str
    oauth_password: str
    server_url: str = ""
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

        values = {
            "CODING_TOOLS_MCP_OAUTH_CLIENT_ID": self.oauth_client_id.strip(),
            "CODING_TOOLS_MCP_OAUTH_CLIENT_SECRET": self.oauth_client_secret.strip(),
            "CODING_TOOLS_MCP_OAUTH_PASSWORD": self.oauth_password.strip(),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise ValueError(
                "缺少 OAuth 配置:\n" + "\n".join(f"  - {key}" for key in missing)
            )

        return LaunchConfig(
            workspace=workspace,
            oauth_client_id=values["CODING_TOOLS_MCP_OAUTH_CLIENT_ID"],
            oauth_client_secret=values["CODING_TOOLS_MCP_OAUTH_CLIENT_SECRET"],
            oauth_password=values["CODING_TOOLS_MCP_OAUTH_PASSWORD"],
            server_url=normalize_server_url(self.server_url),
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
        return cls(
            workspace=workspace,
            oauth_client_id=env.get("CODING_TOOLS_MCP_OAUTH_CLIENT_ID", ""),
            oauth_client_secret=env.get("CODING_TOOLS_MCP_OAUTH_CLIENT_SECRET", ""),
            oauth_password=env.get("CODING_TOOLS_MCP_OAUTH_PASSWORD", ""),
            server_url=env.get("CODING_TOOLS_MCP_SERVER_URL", ""),
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

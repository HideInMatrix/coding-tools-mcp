from __future__ import annotations

from ..cloudflared import CloudflaredTunnel
from ..config import NetworkConfig
from ..process_utils import LogCallback
from .base import NetworkProvider, NetworkProviderResult


class CloudflareProvider(NetworkProvider):
    key = "cloudflare"
    display_name = "Cloudflare Tunnel"

    def __init__(self, log: LogCallback):
        self._tunnel = CloudflaredTunnel(log)

    @property
    def is_running(self) -> bool:
        return bool(self._tunnel.process and self._tunnel.process.poll() is None)

    @property
    def exit_code(self) -> int | None:
        process = self._tunnel.process
        return process.returncode if process is not None else None

    def start(self, host: str, port: int, config: NetworkConfig) -> NetworkProviderResult:
        public_url = config.public_url
        tunnel_token = config.options.get("tunnel_token", "").strip()
        if bool(public_url) != bool(tunnel_token):
            raise ValueError("Cloudflare 固定 Public URL 与 Tunnel Token 必须同时填写；都留空则使用 Quick Tunnel。")
        resolved = self._tunnel.start(
            host,
            port,
            public_url=public_url,
            tunnel_token=tunnel_token,
        )
        return NetworkProviderResult(
            provider=self.key,
            public_base_url=public_url or resolved,
            mode_label="Cloudflare Named Tunnel" if public_url else "Cloudflare Quick Tunnel",
        )

    def stop(self) -> None:
        self._tunnel.stop()

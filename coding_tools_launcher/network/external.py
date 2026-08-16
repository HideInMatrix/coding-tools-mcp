from __future__ import annotations

from ..config import NetworkConfig
from ..process_utils import LogCallback
from .base import NetworkProvider, NetworkProviderResult


class ExternalUrlProvider(NetworkProvider):
    key = "external"
    display_name = "自定义公网 URL"

    def __init__(self, log: LogCallback):
        self._log = log
        self._active = False

    @property
    def is_running(self) -> bool:
        return self._active

    def start(self, host: str, port: int, config: NetworkConfig) -> NetworkProviderResult:
        if not config.public_url:
            raise ValueError("自定义公网 URL 模式必须填写 Public URL。")
        self._active = True
        self._log(f"使用外部网络入口: {config.public_url}")
        self._log(f"请确保该公网地址反向代理到 http://{host}:{port}")
        return NetworkProviderResult(self.key, config.public_url, "External Public URL")

    def stop(self) -> None:
        self._active = False

from __future__ import annotations

from ..process_utils import LogCallback
from .base import NetworkProvider
from .cloudflare import CloudflareProvider
from .external import ExternalUrlProvider
from .frp import FrpProvider
from .ngrok import NgrokProvider
from .tailscale import TailscaleProvider


PROVIDER_TYPES: dict[str, type[NetworkProvider]] = {
    "cloudflare": CloudflareProvider,
    "frp": FrpProvider,
    "ngrok": NgrokProvider,
    "tailscale": TailscaleProvider,
    "external": ExternalUrlProvider,
}


def create_network_provider(provider: str, log: LogCallback) -> NetworkProvider:
    try:
        provider_type = PROVIDER_TYPES[provider]
    except KeyError as exc:
        raise ValueError(f"不支持的网络提供方案: {provider}") from exc
    return provider_type(log)

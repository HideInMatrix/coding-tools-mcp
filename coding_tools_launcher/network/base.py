from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import NetworkConfig


@dataclass(frozen=True, slots=True)
class NetworkProviderResult:
    provider: str
    public_base_url: str
    mode_label: str


class NetworkProvider(ABC):
    """Common contract for every way the local MCP server reaches the internet."""

    key = "base"
    display_name = "Network Provider"

    @abstractmethod
    def start(self, host: str, port: int, config: NetworkConfig) -> NetworkProviderResult:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @property
    @abstractmethod
    def is_running(self) -> bool:
        raise NotImplementedError

    @property
    def exit_code(self) -> int | None:
        return None

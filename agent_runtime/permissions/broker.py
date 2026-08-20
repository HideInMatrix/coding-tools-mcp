"""Adapter for the optional desktop permission broker."""

from __future__ import annotations

from typing import Any

from ..local_permission_broker import (
    LocalPermissionDecision,
    LocalPermissionBrokerClient,
)


class PermissionBroker:
    def __init__(self, client: Any | None = None) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "PermissionBroker":
        return cls(LocalPermissionBrokerClient.from_env())

    def request(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        permission: str,
        message: str,
        principal: str,
    ) -> LocalPermissionDecision:
        if self.client is None:
            return LocalPermissionDecision("unavailable")
        return self.client.request(
            tool_name=name,
            arguments=arguments,
            permission=permission,
            reason=message,
            principal=principal or "anonymous",
        )


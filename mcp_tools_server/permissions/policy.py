"""Permission policy evaluation independent from tool implementations."""

from __future__ import annotations

from collections.abc import Iterable

from .capabilities import Capability, OperationPermission, PermissionProfile


class PermissionPolicy:
    def __init__(self, profile: PermissionProfile) -> None:
        self.profile = profile

    def missing_capabilities(
        self,
        required: Iterable[Capability],
    ) -> frozenset[Capability]:
        return frozenset(required) - self.profile.capabilities

    def operation_is_auto_granted(self, permission: str) -> bool:
        try:
            operation = OperationPermission(permission)
        except ValueError:
            return False
        return operation in self.profile.auto_granted_operations

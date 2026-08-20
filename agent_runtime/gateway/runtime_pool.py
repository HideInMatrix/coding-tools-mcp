"""Lazy Runtime isolation for Local MCP Gateway profiles."""

from __future__ import annotations

import threading
from collections.abc import Callable

from ..runtime import Runtime
from .models import GatewayProfile
from .registry import GatewayProfileRegistry


RuntimeFactory = Callable[[GatewayProfile], Runtime]


def default_runtime_factory(profile: GatewayProfile) -> Runtime:
    return Runtime(
        profile.workspace,
        permission_mode=profile.permission_mode,
        allow_network=profile.allow_network,
        enable_view_image=profile.enable_view_image,
    )


class GatewayRuntimePool:
    """One isolated Runtime/PermissionSession per Gateway Profile."""

    def __init__(
        self,
        registry: GatewayProfileRegistry,
        *,
        factory: RuntimeFactory = default_runtime_factory,
    ) -> None:
        self.registry = registry
        self.factory = factory
        self._lock = threading.RLock()
        self._runtimes: dict[str, Runtime] = {}

    def get(self, profile_id: str) -> Runtime:
        with self._lock:
            existing = self._runtimes.get(profile_id)
            if existing is not None:
                return existing
            profile = self.registry.get(profile_id)
            if profile is None:
                raise KeyError(f"unknown gateway profile: {profile_id}")
            runtime = self.factory(profile)
            self._runtimes[profile_id] = runtime
            return runtime

    def runtime_for_path(self, path: str) -> tuple[GatewayProfile, Runtime] | None:
        route = self.registry.resolve(path)
        if route is None:
            return None
        return route.profile, self.get(route.profile.profile_id)

    def close_profile(self, profile_id: str) -> None:
        with self._lock:
            runtime = self._runtimes.pop(profile_id, None)
        if runtime is not None:
            runtime.close()

    def close(self) -> None:
        with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            runtime.close()


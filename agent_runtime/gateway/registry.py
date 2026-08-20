"""Profile registry used by the Local MCP Gateway."""

from __future__ import annotations

import threading

from .models import GatewayProfile
from .routes import GatewayRoute, GatewayRouteResolver


class GatewayProfileRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_id: dict[str, GatewayProfile] = {}
        self._by_path: dict[str, GatewayProfile] = {}

    def register(self, profile: GatewayProfile) -> GatewayProfile:
        validated = profile.validated()
        with self._lock:
            if validated.profile_id in self._by_id:
                raise ValueError(f"duplicate gateway profile_id: {validated.profile_id}")
            if validated.instance_path in self._by_path:
                raise ValueError(
                    f"duplicate gateway instance_path: {validated.instance_path}"
                )
            self._by_id[validated.profile_id] = validated
            self._by_path[validated.instance_path] = validated
        return validated

    def unregister(self, profile_id: str) -> GatewayProfile | None:
        with self._lock:
            profile = self._by_id.pop(profile_id, None)
            if profile is not None:
                self._by_path.pop(profile.instance_path, None)
            return profile

    def get(self, profile_id: str) -> GatewayProfile | None:
        with self._lock:
            return self._by_id.get(profile_id)

    def profiles(self) -> tuple[GatewayProfile, ...]:
        with self._lock:
            return tuple(self._by_id.values())

    def resolve(self, path: str) -> GatewayRoute | None:
        return GatewayRouteResolver(self.profiles()).resolve(path)

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_id)


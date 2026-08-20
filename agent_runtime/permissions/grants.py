"""In-memory permission grants scoped to one Runtime session."""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any

from .capabilities import ELICITABLE_PERMISSIONS
from .state import arguments_digest


class PermissionGrantStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._grants: dict[str, dict[str, Any]] = {}
        self._session_principals: set[str] = set()

    def store(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        permission: str,
        principal: str,
        scope: str,
        ttl_seconds: int,
    ) -> tuple[str, int]:
        now = int(time.time())
        expires_at = now + max(1, min(int(ttl_seconds), 3_600))
        grant_id = f"ctg_{secrets.token_urlsafe(18)}"
        record = {
            "tool_name": tool_name,
            "arguments_hash": arguments_digest(tool_name, arguments),
            "permission": permission,
            "principal": principal or "anonymous",
            "scope": "session" if scope == "session" else "once",
            "expires_at": expires_at,
        }
        with self._lock:
            self._grants[grant_id] = record
        return grant_id, expires_at

    def permissions_for_call(
        self,
        name: str,
        arguments: dict[str, Any],
        principal: str,
    ) -> frozenset[str]:
        now = int(time.time())
        normalized_principal = principal or "anonymous"
        digest = arguments_digest(name, arguments)
        granted: set[str] = set()
        consume: list[str] = []
        with self._lock:
            for grant_id, record in list(self._grants.items()):
                if int(record.get("expires_at", 0)) < now:
                    self._grants.pop(grant_id, None)
                    continue
                if (
                    record.get("tool_name") != name
                    or record.get("arguments_hash") != digest
                    or record.get("principal") != normalized_principal
                ):
                    continue
                permission = record.get("permission")
                if isinstance(permission, str) and permission in ELICITABLE_PERMISSIONS:
                    granted.add(permission)
                    if record.get("scope") == "once":
                        consume.append(grant_id)
            for grant_id in consume:
                self._grants.pop(grant_id, None)
        return frozenset(granted)

    def session_permissions(self, principal: str) -> frozenset[str]:
        with self._lock:
            if (principal or "anonymous") in self._session_principals:
                return ELICITABLE_PERMISSIONS
        return frozenset()

    def grant_session(self, principal: str) -> None:
        with self._lock:
            self._session_principals.add(principal or "anonymous")


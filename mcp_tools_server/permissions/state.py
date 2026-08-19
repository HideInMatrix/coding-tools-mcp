"""Signed, single-use permission requestState handling."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from ..errors import RpcError
from .capabilities import ELICITABLE_PERMISSIONS


PERMISSION_STATE_TTL_SECONDS = 300


def arguments_digest(name: str, arguments: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"tool": name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class PermissionStateStore:
    """Issues and consumes requestState values bound to one Runtime workspace."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = str(workspace)
        self._secret = secrets.token_bytes(32)
        self._lock = threading.RLock()
        self._consumed: dict[str, float] = {}

    def mint(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        permission: str,
        principal: str,
        granted: frozenset[str],
    ) -> str:
        payload = {
            "v": 1,
            "tool": name,
            "arguments_hash": arguments_digest(name, arguments),
            "permission": permission,
            "granted": sorted(granted),
            "workspace": self.workspace,
            "principal": principal or "anonymous",
            "exp": int(time.time()) + PERMISSION_STATE_TTL_SECONDS,
            "nonce": secrets.token_urlsafe(12),
        }
        encoded = _b64url_encode(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signature = _b64url_encode(
            hmac.new(
                self._secret,
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        return f"ctpg1.{encoded}.{signature}"

    def verify(
        self,
        state: str,
        *,
        name: str,
        arguments: dict[str, Any],
        principal: str,
    ) -> dict[str, Any]:
        try:
            prefix, encoded, signature = state.split(".", 2)
            if prefix != "ctpg1":
                raise ValueError("unknown state prefix")
            expected = _b64url_encode(
                hmac.new(
                    self._secret,
                    encoded.encode("ascii"),
                    hashlib.sha256,
                ).digest()
            )
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid state signature")
            raw = json.loads(_b64url_decode(encoded).decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("state payload must be an object")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RpcError(
                -32602,
                "Invalid permission requestState",
                {"reason": "permission_state_invalid"},
            ) from exc

        now = int(time.time())
        if int(raw.get("exp", 0)) < now:
            raise RpcError(
                -32602,
                "Permission requestState has expired",
                {"reason": "permission_state_expired"},
            )
        if raw.get("tool") != name or raw.get("arguments_hash") != arguments_digest(name, arguments):
            raise RpcError(
                -32602,
                "Permission requestState does not match this tool call",
                {"reason": "permission_state_binding"},
            )
        if raw.get("workspace") != self.workspace:
            raise RpcError(
                -32602,
                "Permission requestState does not match this workspace",
                {"reason": "permission_state_workspace"},
            )
        if raw.get("principal") != (principal or "anonymous"):
            raise RpcError(
                -32602,
                "Permission requestState does not match the authenticated principal",
                {"reason": "permission_state_principal"},
            )
        permission = raw.get("permission")
        if not isinstance(permission, str) or permission not in ELICITABLE_PERMISSIONS:
            raise RpcError(
                -32602,
                "Permission requestState contains an unsupported permission",
                {"reason": "permission_state_permission"},
            )
        return raw

    def consume(self, state: str, expires_at: int) -> None:
        state_id = hashlib.sha256(state.encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock:
            expired = [
                key
                for key, expiry in self._consumed.items()
                if expiry <= now
            ]
            for key in expired:
                self._consumed.pop(key, None)
            if state_id in self._consumed:
                raise RpcError(
                    -32602,
                    "Permission requestState has already been consumed",
                    {"reason": "permission_state_replay"},
                )
            self._consumed[state_id] = float(expires_at)


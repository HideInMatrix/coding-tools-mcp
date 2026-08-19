from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BROKER_DIR_ENV = "CODING_TOOLS_MCP_PERMISSION_BROKER_DIR"
BROKER_SECRET_ENV = "CODING_TOOLS_MCP_PERMISSION_BROKER_SECRET"
BROKER_SERVER_ID_ENV = "CODING_TOOLS_MCP_PERMISSION_BROKER_SERVER_ID"
BROKER_VERSION = 1
BROKER_REQUEST_TTL_SECONDS = 120
_SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|credential|api[_-]?key|password|passwd|private)",
    re.I,
)


def canonical_payload(payload: dict[str, Any]) -> bytes:
    value = {key: item for key, item in payload.items() if key != "signature"}
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_payload(secret: bytes, payload: dict[str, Any]) -> str:
    return hmac.new(secret, canonical_payload(payload), hashlib.sha256).hexdigest()


def verify_payload(secret: bytes, payload: dict[str, Any]) -> bool:
    signature = payload.get("signature")
    return isinstance(signature, str) and hmac.compare_digest(
        signature,
        sign_payload(secret, payload),
    )


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def redact_for_display(value: Any, *, key: str = "") -> Any:
    if key and _SENSITIVE_KEY_RE.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(child_key): redact_for_display(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_for_display(item) for item in value[:100]]
    if isinstance(value, str):
        return value if len(value) <= 1200 else value[:1197] + "..."
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1200]


@dataclass(frozen=True, slots=True)
class LocalPermissionDecision:
    status: str
    scope: str = "once"

    @property
    def approved(self) -> bool:
        return self.status == "approved"

    @property
    def denied(self) -> bool:
        return self.status == "denied"

    @property
    def session(self) -> bool:
        return self.approved and self.scope == "session"


class LocalPermissionBrokerClient:
    def __init__(self, directory: Path, secret: bytes, server_id: str) -> None:
        self.directory = directory.resolve()
        self.secret = secret
        self.server_id = server_id

    @classmethod
    def from_env(cls) -> "LocalPermissionBrokerClient | None":
        raw_directory = os.environ.get(BROKER_DIR_ENV, "").strip()
        raw_secret = os.environ.get(BROKER_SECRET_ENV, "").strip()
        if not raw_directory or not raw_secret:
            return None
        try:
            directory = Path(raw_directory).expanduser().resolve()
            secret = bytes.fromhex(raw_secret)
        except (OSError, ValueError):
            return None
        if len(secret) != 32 or not directory.is_dir():
            return None
        return cls(
            directory,
            secret,
            os.environ.get(BROKER_SERVER_ID_ENV, "").strip(),
        )

    def request(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        permission: str,
        reason: str,
        principal: str,
        timeout_seconds: int = BROKER_REQUEST_TTL_SECONDS,
    ) -> LocalPermissionDecision:
        timeout = max(1, min(int(timeout_seconds), BROKER_REQUEST_TTL_SECONDS))
        now = int(time.time())
        request_id = secrets.token_urlsafe(24)
        request_path = self.directory / f"{request_id}.request.json"
        response_path = self.directory / f"{request_id}.response.json"
        payload: dict[str, Any] = {
            "version": BROKER_VERSION,
            "request_id": request_id,
            "server_id": self.server_id,
            "tool_name": tool_name,
            "permission": permission,
            "reason": str(reason)[:1200],
            "arguments": redact_for_display(arguments),
            "arguments_hash": hashlib.sha256(canonical_payload({"arguments": arguments})).hexdigest(),
            "principal_hash": hashlib.sha256((principal or "anonymous").encode("utf-8")).hexdigest(),
            "created_at": now,
            "expires_at": now + timeout,
            "pid": os.getpid(),
        }
        payload["signature"] = sign_payload(self.secret, payload)
        try:
            atomic_json_write(request_path, payload)
        except OSError:
            return LocalPermissionDecision("unavailable")

        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                try:
                    raw = json.loads(response_path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    time.sleep(0.1)
                    continue
                except (OSError, json.JSONDecodeError):
                    return LocalPermissionDecision("unavailable")
                if not isinstance(raw, dict) or not verify_payload(self.secret, raw):
                    return LocalPermissionDecision("unavailable")
                if raw.get("request_id") != request_id:
                    return LocalPermissionDecision("unavailable")
                approved = raw.get("approved") is True
                scope = "session" if raw.get("scope") == "session" else "once"
                return LocalPermissionDecision(
                    "approved" if approved else "denied",
                    scope=scope if approved else "once",
                )
            return LocalPermissionDecision("timeout")
        finally:
            for path in (request_path, response_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


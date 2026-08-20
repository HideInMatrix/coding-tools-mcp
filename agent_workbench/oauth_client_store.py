from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .oauth_persistence import (
    _validated_server_id,
    bound_server_oauth_issuer,
    issuer_oauth_directory,
    server_oauth_directory,
)


@dataclass(frozen=True, slots=True)
class OAuthClientSummary:
    client_id: str
    client_name: str | None
    redirect_uris: tuple[str, ...]
    token_endpoint_auth_method: str
    issued_at: int
    client_type: str = "dcr"
    revocable: bool = True


class OAuthClientStore:
    """Manage persisted OAuth clients for a stopped persistent Server Profile.

    Live Server processes own an in-memory registry. The desktop manager must
    therefore stop that Server before mutating this file, otherwise a later
    registration write from the child process could overwrite the change.
    """

    def __init__(self, server_id: str, path: Path | None = None) -> None:
        self.server_id = _validated_server_id(server_id)
        if path is not None:
            self.path = path
            return
        issuer = bound_server_oauth_issuer(self.server_id)
        if issuer:
            self.path = issuer_oauth_directory(issuer) / "clients.json"
        else:
            # Backward-compatible fallback before the first issuer-aware start.
            self.path = server_oauth_directory(self.server_id) / "clients.json"

    def list(self) -> list[OAuthClientSummary]:
        payload = self._read_payload()
        clients: list[OAuthClientSummary] = []
        for item in payload["clients"]:
            clients.append(
                OAuthClientSummary(
                    client_id=str(item["client_id"]),
                    client_name=(
                        str(item["client_name"])
                        if item.get("client_name") is not None
                        else None
                    ),
                    redirect_uris=tuple(str(value) for value in item["redirect_uris"]),
                    token_endpoint_auth_method=str(
                        item["token_endpoint_auth_method"]
                    ),
                    issued_at=int(item["issued_at"]),
                )
            )
        return sorted(clients, key=lambda item: (item.issued_at, item.client_id))

    def remove(self, client_id: str) -> bool:
        target = client_id.strip()
        if not target:
            return False
        payload = self._read_payload()
        before = len(payload["clients"])
        payload["clients"] = [
            item for item in payload["clients"] if str(item.get("client_id", "")) != target
        ]
        if len(payload["clients"]) == before:
            return False
        self._write_payload(payload)
        return True

    def clear(self) -> int:
        payload = self._read_payload()
        count = len(payload["clients"])
        if count:
            payload["clients"] = []
            self._write_payload(payload)
        return count

    def _read_payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "clients": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"OAuth client registry 文件损坏: {self.path}") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise RuntimeError(f"OAuth client registry 格式不受支持: {self.path}")
        clients = payload.get("clients")
        if not isinstance(clients, list):
            raise RuntimeError(f"OAuth client registry clients 字段无效: {self.path}")
        for item in clients:
            if not isinstance(item, dict):
                raise RuntimeError(f"OAuth client registry Client 数据无效: {self.path}")
            required = {
                "client_id",
                "redirect_uris",
                "token_endpoint_auth_method",
                "issued_at",
            }
            if not required.issubset(item):
                raise RuntimeError(f"OAuth client registry Client 字段缺失: {self.path}")
            if not isinstance(item["redirect_uris"], list):
                raise RuntimeError(f"OAuth client registry redirect_uris 无效: {self.path}")
        return payload

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            if os.name != "nt":
                temporary_path.chmod(0o600)
            os.replace(temporary_path, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
        finally:
            if temporary_path.exists():
                temporary_path.unlink(missing_ok=True)


class CIMDClientStore:
    """Read the best-effort CIMD observation sidecar for the desktop UI."""

    def __init__(self, server_id: str, path: Path | None = None) -> None:
        self.server_id = _validated_server_id(server_id)
        if path is not None:
            self.path = path
            return
        issuer = bound_server_oauth_issuer(self.server_id)
        if issuer:
            self.path = issuer_oauth_directory(issuer) / "cimd-clients.json"
        else:
            self.path = server_oauth_directory(self.server_id) / "cimd-clients.json"

    def list(self) -> list[OAuthClientSummary]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return []
        raw_clients = payload.get("clients")
        if not isinstance(raw_clients, list):
            return []

        clients: list[OAuthClientSummary] = []
        for item in raw_clients:
            if not isinstance(item, dict):
                continue
            client_id = item.get("client_id")
            if not isinstance(client_id, str) or not client_id:
                continue
            redirect_uris = item.get("redirect_uris", [])
            clients.append(
                OAuthClientSummary(
                    client_id=client_id,
                    client_name=(
                        str(item["client_name"])
                        if item.get("client_name") is not None
                        else None
                    ),
                    redirect_uris=(
                        tuple(str(value) for value in redirect_uris)
                        if isinstance(redirect_uris, list)
                        else ()
                    ),
                    token_endpoint_auth_method=str(
                        item.get("token_endpoint_auth_method") or ""
                    ),
                    issued_at=int(item.get("observed_at") or 0),
                    client_type="cimd",
                    revocable=False,
                )
            )
        return sorted(clients, key=lambda item: (item.issued_at, item.client_id))

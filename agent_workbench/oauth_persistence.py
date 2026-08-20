from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .user_settings import settings_dir


OAUTH_REGISTRY_FILE_ENV = "AGENT_RUNTIME_OAUTH_CLIENT_REGISTRY_FILE"
OAUTH_TOKEN_SECRET_ENV = "AGENT_RUNTIME_OAUTH_TOKEN_SECRET"


@dataclass(frozen=True, slots=True)
class OAuthPersistence:
    registry_file: Path
    token_secret_hex: str
    storage_dir: Path | None = None
    ephemeral: bool = False

    def cleanup(self) -> None:
        if self.ephemeral and self.storage_dir is not None:
            shutil.rmtree(self.storage_dir, ignore_errors=True)


def canonical_oauth_issuer(server_url: str) -> str:
    """Canonicalize the OAuth Authorization Server issuer/base URL."""

    raw = str(server_url or "").strip().rstrip("/")
    if raw.endswith("/mcp"):
        raw = raw[:-4].rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("OAuth issuer 必须是完整的 http/https URL。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("OAuth issuer 不能包含用户信息、query 或 fragment。")
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = host if port in {None, default_port} else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


def _oauth_dir() -> Path:
    path = settings_dir() / "oauth"
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def _issuer_oauth_root() -> Path:
    path = _oauth_dir() / "issuers"
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def issuer_oauth_directory(issuer: str) -> Path:
    canonical = canonical_oauth_issuer(issuer)
    key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return _issuer_oauth_root() / key


def _write_issuer_metadata(directory: Path, issuer: str) -> None:
    metadata_file = directory / "issuer.json"
    canonical = canonical_oauth_issuer(issuer)
    if metadata_file.exists():
        try:
            payload = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"OAuth issuer metadata 文件损坏: {metadata_file}") from exc
        if not isinstance(payload, dict) or payload.get("issuer") != canonical:
            raise RuntimeError(f"OAuth issuer metadata 与目录不匹配: {metadata_file}")
        return
    _atomic_write_json(
        metadata_file,
        {"version": 1, "issuer": canonical},
    )


def prepare_issuer_oauth_persistence(issuer: str) -> OAuthPersistence:
    """Return OAuth state keyed by Authorization Server issuer identity."""

    canonical = canonical_oauth_issuer(issuer)
    directory = issuer_oauth_directory(canonical)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    _write_issuer_metadata(directory, canonical)
    secret_file = directory / "token-secret"
    registry_file = directory / "clients.json"
    return OAuthPersistence(
        registry_file=registry_file,
        token_secret_hex=_load_or_create_token_secret(secret_file),
        storage_dir=directory,
        ephemeral=False,
    )


def delete_issuer_oauth_storage(issuer: str) -> None:
    """Delete OAuth state for one issuer after an explicit profile removal."""

    shutil.rmtree(issuer_oauth_directory(issuer), ignore_errors=True)


def _read_token_secret(path: Path) -> str:
    try:
        value = path.read_text(encoding="ascii").strip()
        decoded = bytes.fromhex(value)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"OAuth token secret 文件损坏: {path}") from exc
    if len(decoded) < 32:
        raise RuntimeError(f"OAuth token secret 长度不足 32 字节: {path}")
    return value


def _load_or_create_token_secret(path: Path) -> str:
    if path.exists():
        return _read_token_secret(path)

    value = secrets.token_bytes(32).hex()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return _read_token_secret(path)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(value)
            handle.write("\n")
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    if os.name != "nt":
        path.chmod(0o600)
    return value


_SERVER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _validated_server_id(server_id: str) -> str:
    value = server_id.strip()
    if not _SERVER_ID_PATTERN.fullmatch(value):
        raise ValueError("server_id 只能包含字母、数字、下划线和连字符。")
    return value


def server_oauth_directory(server_id: str) -> Path:
    validated_id = _validated_server_id(server_id)
    return settings_dir() / "servers" / validated_id / "oauth"


def server_oauth_binding_file(server_id: str) -> Path:
    validated_id = _validated_server_id(server_id)
    return settings_dir() / "servers" / validated_id / "oauth-issuer.json"


def bind_server_oauth_issuer(server_id: str, issuer: str) -> None:
    """Store only the management binding from a profile to an OAuth issuer."""

    if not server_id.strip():
        return
    path = server_oauth_binding_file(server_id)
    _atomic_write_json(
        path,
        {
            "version": 1,
            "issuer": canonical_oauth_issuer(issuer),
        },
    )


def bound_server_oauth_issuer(server_id: str) -> str | None:
    path = server_oauth_binding_file(server_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"OAuth issuer binding 文件损坏: {path}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RuntimeError(f"OAuth issuer binding 格式不受支持: {path}")
    issuer = payload.get("issuer")
    if not isinstance(issuer, str) or not issuer:
        raise RuntimeError(f"OAuth issuer binding 缺少 issuer: {path}")
    return canonical_oauth_issuer(issuer)


def delete_server_oauth_storage(server_id: str) -> None:
    directory = server_oauth_directory(server_id)
    shutil.rmtree(directory, ignore_errors=True)
    server_oauth_binding_file(server_id).unlink(missing_ok=True)


def prepare_ephemeral_oauth_persistence(server_id: str) -> OAuthPersistence:
    """Create OAuth state for one disposable Server runtime session."""

    validated_id = _validated_server_id(server_id)
    directory = Path(
        tempfile.mkdtemp(prefix=f"micromatrix-workbench-{validated_id[:16]}-oauth-")
    )
    if os.name != "nt":
        directory.chmod(0o700)
    secret_file = directory / "token-secret"
    registry_file = directory / "clients.json"
    return OAuthPersistence(
        registry_file=registry_file,
        token_secret_hex=_load_or_create_token_secret(secret_file),
        storage_dir=directory,
        ephemeral=True,
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        if os.name != "nt":
            temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink(missing_ok=True)




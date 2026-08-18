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


OAUTH_REGISTRY_FILE_ENV = "CODING_TOOLS_MCP_OAUTH_CLIENT_REGISTRY_FILE"
OAUTH_TOKEN_SECRET_ENV = "CODING_TOOLS_MCP_OAUTH_TOKEN_SECRET"


@dataclass(frozen=True, slots=True)
class OAuthPersistence:
    registry_file: Path
    token_secret_hex: str
    storage_dir: Path | None = None
    ephemeral: bool = False

    def cleanup(self) -> None:
        if self.ephemeral and self.storage_dir is not None:
            shutil.rmtree(self.storage_dir, ignore_errors=True)


def _storage_key(server_url: str) -> str:
    normalized = server_url.rstrip("/").lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


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


def prepare_oauth_persistence(server_url: str) -> OAuthPersistence:
    """Return legacy URL-keyed OAuth storage.

    Kept only for migration/backward compatibility. New persistent launches
    use ``prepare_issuer_oauth_persistence``.
    """

    directory = _oauth_dir()
    key = _storage_key(server_url)
    secret_file = directory / f"{key}.token-secret"
    registry_file = directory / f"{key}.clients.json"
    return OAuthPersistence(
        registry_file=registry_file,
        token_secret_hex=_load_or_create_token_secret(secret_file),
        storage_dir=directory,
    )


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


def prepare_server_oauth_persistence(server_id: str) -> OAuthPersistence:
    """Return the legacy Server-Profile-keyed OAuth storage for migration."""

    directory = server_oauth_directory(server_id)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    secret_file = directory / "token-secret"
    registry_file = directory / "clients.json"
    return OAuthPersistence(
        registry_file=registry_file,
        token_secret_hex=_load_or_create_token_secret(secret_file),
        storage_dir=directory,
        ephemeral=False,
    )


def delete_server_oauth_storage(server_id: str) -> None:
    directory = server_oauth_directory(server_id)
    shutil.rmtree(directory, ignore_errors=True)
    server_oauth_binding_file(server_id).unlink(missing_ok=True)


def migrate_url_keyed_oauth_storage(server_id: str, server_url: str) -> bool:
    """Copy legacy URL-keyed OAuth state into one persistent Server Profile.

    The migration is intentionally non-destructive and idempotent: legacy
    files are never removed, and existing server-id keyed files are never
    overwritten. This keeps rollback possible while preventing an upgrade from
    losing dynamically registered client ids for a fixed public URL.
    """

    normalized_url = server_url.strip().rstrip("/")
    if not normalized_url:
        return False

    legacy_directory = _oauth_dir()
    legacy_key = _storage_key(normalized_url)
    legacy_registry = legacy_directory / f"{legacy_key}.clients.json"
    legacy_secret = legacy_directory / f"{legacy_key}.token-secret"
    if not legacy_registry.exists() and not legacy_secret.exists():
        return False

    target_directory = server_oauth_directory(server_id)
    target_directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        target_directory.chmod(0o700)

    copied = False
    for source, destination_name in (
        (legacy_registry, "clients.json"),
        (legacy_secret, "token-secret"),
    ):
        if not source.exists():
            continue
        destination = target_directory / destination_name
        if destination.exists():
            continue
        shutil.copy2(source, destination)
        if os.name != "nt":
            destination.chmod(0o600)
        copied = True
    return copied


def migrate_oauth_storage_to_issuer(
    issuer: str,
    *,
    server_id: str = "",
) -> bool:
    """Migrate historical OAuth state into issuer-keyed storage.

    Migration is non-destructive and runs safely on every persistent launch.
    Existing issuer-keyed files win. Historical server-id keyed storage is
    preferred over the older URL-keyed layout because it is the most recent
    representation used by the desktop manager.
    """

    canonical = canonical_oauth_issuer(issuer)
    target = issuer_oauth_directory(canonical)
    target.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        target.chmod(0o700)
    _write_issuer_metadata(target, canonical)

    source_directories: list[Path] = []
    if server_id.strip():
        source_directories.append(server_oauth_directory(server_id))

    legacy_root = _oauth_dir()
    legacy_urls = (canonical, f"{canonical}/mcp")
    for legacy_url in legacy_urls:
        key = _storage_key(legacy_url)
        # URL-keyed storage used flat files instead of a directory. Represent
        # it with a sentinel path handled below.
        source_directories.append(legacy_root / f"__url_keyed__{key}")

    copied = False
    for destination_name in ("clients.json", "token-secret"):
        destination = target / destination_name
        if destination.exists():
            continue
        for source_directory in source_directories:
            if source_directory.name.startswith("__url_keyed__"):
                key = source_directory.name.removeprefix("__url_keyed__")
                suffix = "clients.json" if destination_name == "clients.json" else "token-secret"
                source = legacy_root / f"{key}.{suffix}"
            else:
                source = source_directory / destination_name
            if not source.exists():
                continue
            shutil.copy2(source, destination)
            if os.name != "nt":
                destination.chmod(0o600)
            copied = True
            break
    return copied


def prepare_ephemeral_oauth_persistence(server_id: str) -> OAuthPersistence:
    """Create OAuth state for one disposable Server runtime session."""

    validated_id = _validated_server_id(server_id)
    directory = Path(
        tempfile.mkdtemp(prefix=f"coding-tools-mcp-{validated_id[:16]}-oauth-")
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


def install_oauth_registry_persistence() -> None:
    """Attach JSON persistence to the in-tree OAuth client registry.

    The MCP runtime keeps its registry in memory by design; the desktop launcher
    adds persistence because a fixed OAuth issuer should keep dynamically
    registered clients valid across application restarts and upgrades.
    """

    if not os.environ.get(OAUTH_REGISTRY_FILE_ENV, "").strip():
        return

    from mcp_tools_server.oauth import OAuthClient, OAuthClientRegistry

    if getattr(OAuthClientRegistry, "_launcher_persistence_installed", False):
        return

    original_init = OAuthClientRegistry.__init__
    original_register = OAuthClientRegistry.register
    original_remove = OAuthClientRegistry.remove
    original_clear = OAuthClientRegistry.clear

    def current_registry_path() -> Path | None:
        raw_path = os.environ.get(OAUTH_REGISTRY_FILE_ENV, "").strip()
        if not raw_path:
            return None
        return Path(raw_path).expanduser()

    def load_clients(registry: Any) -> None:
        registry_path = current_registry_path()
        if registry_path is None:
            return
        if not registry_path.exists():
            return
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"OAuth client registry 文件损坏: {registry_path}") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise RuntimeError(f"OAuth client registry 格式不受支持: {registry_path}")
        raw_clients = payload.get("clients", [])
        if not isinstance(raw_clients, list):
            raise RuntimeError(f"OAuth client registry clients 字段无效: {registry_path}")

        restored: dict[str, OAuthClient] = {}
        try:
            for item in raw_clients:
                if not isinstance(item, dict):
                    raise ValueError("client entry must be an object")
                client_id = str(item["client_id"])
                redirect_uris = tuple(str(value) for value in item["redirect_uris"])
                auth_method = str(item["token_endpoint_auth_method"])
                client_name_value = item.get("client_name")
                secret_digest_value = item.get("secret_digest")
                application_type = str(item.get("application_type", "web"))
                restored[client_id] = OAuthClient(
                    client_id=client_id,
                    redirect_uris=redirect_uris,
                    token_endpoint_auth_method=auth_method,
                    client_name=(
                        str(client_name_value) if client_name_value is not None else None
                    ),
                    secret_digest=(
                        str(secret_digest_value)
                        if secret_digest_value is not None
                        else None
                    ),
                    issued_at=int(item["issued_at"]),
                    application_type=application_type,
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"OAuth client registry 内容无效: {registry_path}") from exc

        with registry._lock:
            registry._clients.update(restored)

    def save_clients(registry: Any) -> None:
        registry_path = current_registry_path()
        if registry_path is None:
            return
        with registry._lock:
            clients = list(registry._clients.values())
        payload = {
            "version": 1,
            "clients": [
                {
                    "client_id": client.client_id,
                    "redirect_uris": list(client.redirect_uris),
                    "token_endpoint_auth_method": client.token_endpoint_auth_method,
                    "client_name": client.client_name,
                    "secret_digest": client.secret_digest,
                    "issued_at": client.issued_at,
                    "application_type": client.application_type,
                }
                for client in clients
            ],
        }
        _atomic_write_json(registry_path, payload)

    def persistent_init(registry: Any) -> None:
        original_init(registry)
        load_clients(registry)

    def persistent_register(registry: Any, metadata: dict[str, Any]) -> dict[str, Any]:
        response = original_register(registry, metadata)
        save_clients(registry)
        return response

    def persistent_remove(registry: Any, client_id: str) -> bool:
        removed = original_remove(registry, client_id)
        if removed:
            save_clients(registry)
        return removed

    def persistent_clear(registry: Any) -> int:
        count = original_clear(registry)
        if count:
            save_clients(registry)
        return count

    OAuthClientRegistry.__init__ = persistent_init
    OAuthClientRegistry.register = persistent_register
    OAuthClientRegistry.remove = persistent_remove
    OAuthClientRegistry.clear = persistent_clear
    OAuthClientRegistry._launcher_persistence_installed = True


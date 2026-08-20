"""Client ID Metadata Document resolution with SSRF-safe HTTPS fetching."""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
import urllib.parse
from typing import Any

try:
    import certifi
except ImportError:  # pragma: no cover - source-only minimal installs may omit it
    certifi = None

from .oauth import (
    OAuthClient,
    client_from_metadata_document,
    is_client_id_metadata_url,
)
from .oauth_service import OAuthService


CIMD_MAX_BYTES = 64 * 1024
CIMD_TIMEOUT_SECONDS = 5.0
CIMD_DEFAULT_CACHE_SECONDS = 300
CIMD_MAX_CACHE_SECONDS = 3600
CIMD_MAX_REDIRECTS = 3
_TUN_FAKE_IPV4_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_TUN_FAKE_IPV6_NETWORKS = (
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("::ffff:0:0:0/96"),
)


def _is_tun_fake_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Recognize RFC 2544 addresses used by Clash/sing-box fake-IP DNS."""

    if isinstance(address, ipaddress.IPv4Address):
        return address in _TUN_FAKE_IPV4_NETWORK
    for network in _TUN_FAKE_IPV6_NETWORKS:
        if address in network:
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
            return embedded in _TUN_FAKE_IPV4_NETWORK
    return False


def _safe_cimd_destination(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if _is_tun_fake_ip(address):
        return True
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def public_ip_for_host(host: str, port: int) -> str:
    """Resolve a CIMD hostname while rejecting private/unsafe destinations."""

    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("CIMD hostname could not be resolved") from exc
        addresses = []
        for info in infos:
            raw = info[4][0]
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if address not in addresses:
                addresses.append(address)
    if not addresses:
        raise ValueError("CIMD hostname resolved to no usable address")
    if not all(_safe_cimd_destination(address) for address in addresses):
        raise ValueError("CIMD metadata URL must resolve only to public IP addresses")
    return str(addresses[0])


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that pins validated DNS while preserving SNI."""

    def __init__(self, host: str, port: int, connect_ip: str, timeout: float):
        context = ssl.create_default_context()
        if certifi is not None:
            context.load_verify_locations(cafile=certifi.where())
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._connect_ip = connect_ip

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._connect_ip, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _cache_seconds(headers: Any) -> int:
    cache_control = str(headers.get("Cache-Control", ""))
    for item in cache_control.split(","):
        key, separator, value = item.strip().partition("=")
        if separator and key.lower() == "max-age":
            try:
                return max(
                    0,
                    min(int(value.strip().strip('"')), CIMD_MAX_CACHE_SECONDS),
                )
            except ValueError:
                break
    return CIMD_DEFAULT_CACHE_SECONDS


def fetch_cimd_document(client_id: str) -> tuple[dict[str, Any], int]:
    """Fetch a CIMD document with HTTPS, DNS pinning and bounded redirects."""

    current = client_id
    for _ in range(CIMD_MAX_REDIRECTS + 1):
        parsed = urllib.parse.urlsplit(current)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError(
                "CIMD metadata URL must be an HTTPS URL without credentials or fragment"
            )
        port = parsed.port or 443
        connect_ip = public_ip_for_host(parsed.hostname, port)
        connection = PinnedHTTPSConnection(
            parsed.hostname,
            port,
            connect_ip,
            CIMD_TIMEOUT_SECONDS,
        )
        path = urllib.parse.urlunsplit(
            ("", "", parsed.path or "/", parsed.query, "")
        )
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "MicroMatrix-Workbench-CIMD/1",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location", "").strip()
                response.read()
                if not location:
                    raise ValueError("CIMD redirect is missing Location")
                current = urllib.parse.urljoin(current, location)
                continue
            if response.status != 200:
                response.read()
                raise ValueError(f"CIMD metadata returned HTTP {response.status}")
            raw = response.read(CIMD_MAX_BYTES + 1)
            if len(raw) > CIMD_MAX_BYTES:
                raise ValueError("CIMD metadata document is too large")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("CIMD metadata is not valid UTF-8 JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError("CIMD metadata must be a JSON object")
            return payload, _cache_seconds(response.headers)
        finally:
            connection.close()
    raise ValueError("CIMD metadata redirected too many times")


class CIMDClientResolver:
    """Resolve registered or CIMD OAuth clients with a bounded metadata cache."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[OAuthClient, float]] = {}
        self._lock = threading.RLock()

    def resolve(self, config: OAuthService, client_id: str) -> OAuthClient | None:
        registered = config.registry.get(client_id)
        if registered is not None:
            return registered
        if not config.cimd_enabled or not is_client_id_metadata_url(client_id):
            return None

        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(client_id)
            if cached is not None and cached[1] > now:
                config.observed_clients.observe_client(cached[0])
                return cached[0]

        metadata, ttl = fetch_cimd_document(client_id)
        client = client_from_metadata_document(client_id, metadata)
        with self._lock:
            self._cache[client_id] = (client, now + ttl)
        config.observed_clients.observe_client(client)
        return client


DEFAULT_CIMD_CLIENT_RESOLVER = CIMDClientResolver()


def resolve_oauth_client(config: OAuthService, client_id: str) -> OAuthClient | None:
    return DEFAULT_CIMD_CLIENT_RESOLVER.resolve(config, client_id)


__all__ = [
    "CIMDClientResolver",
    "DEFAULT_CIMD_CLIENT_RESOLVER",
    "PinnedHTTPSConnection",
    "fetch_cimd_document",
    "public_ip_for_host",
    "resolve_oauth_client",
]

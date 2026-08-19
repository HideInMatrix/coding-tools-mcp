"""Path ownership resolution for Local MCP Gateway profiles."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .models import GatewayProfile


AUTHORIZATION_METADATA_PREFIX = "/.well-known/oauth-authorization-server"
PROTECTED_RESOURCE_METADATA_PREFIX = "/.well-known/oauth-protected-resource"
OPENID_CONFIGURATION_PREFIX = "/.well-known/openid-configuration"


@dataclass(frozen=True, slots=True)
class GatewayRoute:
    profile: GatewayProfile
    kind: str
    path: str


def request_path(value: str) -> str:
    parsed = urlsplit(value)
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


class GatewayRouteResolver:
    """Resolve external MCP/OAuth paths to one registered profile.

    Direct profile routes use the instance path prefix. OAuth well-known
    metadata inserts the instance path after the RFC-defined prefix, so those
    routes are resolved separately.
    """

    def __init__(self, profiles: tuple[GatewayProfile, ...]) -> None:
        self._profiles = tuple(
            sorted(
                profiles,
                key=lambda profile: len(profile.instance_path),
                reverse=True,
            )
        )

    def resolve(self, value: str) -> GatewayRoute | None:
        path = request_path(value).rstrip("/") or "/"
        for profile in self._profiles:
            instance = profile.instance_path
            authorization = AUTHORIZATION_METADATA_PREFIX + instance
            if path == authorization or path.startswith(authorization + "/"):
                return GatewayRoute(profile, "oauth_authorization_metadata", path)

            openid = OPENID_CONFIGURATION_PREFIX + instance
            if path == openid or path.startswith(openid + "/"):
                return GatewayRoute(profile, "oauth_authorization_metadata", path)

            profile_openid = instance + OPENID_CONFIGURATION_PREFIX
            if path == profile_openid or path.startswith(profile_openid + "/"):
                return GatewayRoute(profile, "oauth_authorization_metadata", path)

            protected = PROTECTED_RESOURCE_METADATA_PREFIX + instance
            if path == protected or path.startswith(protected + "/"):
                return GatewayRoute(profile, "oauth_protected_resource_metadata", path)

            if path == instance or path.startswith(instance + "/"):
                return GatewayRoute(profile, "profile", path)
        return None


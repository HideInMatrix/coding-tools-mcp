"""Security capabilities and permission profiles for MCP tools.

Capabilities describe *what a tool can do*. Operation permissions describe
runtime escalations that may be requested for a concrete invocation. Keeping
the two concepts separate prevents MCP annotations or tool placement from
becoming the security policy itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Capability(StrEnum):
    SYSTEM_INSPECT = "system.inspect"
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    PROCESS_EXECUTE = "process.execute"
    PROCESS_CONTROL = "process.control"
    GIT_READ = "git.read"
    TOOLCHAIN_DISCOVER = "toolchain.discover"
    PERMISSION_MANAGE = "permission.manage"
    MEDIA_READ = "media.read"


class OperationPermission(StrEnum):
    NETWORK = "network"
    DESTRUCTIVE_COMMAND = "destructive_command"
    GIT_METADATA_WRITE = "git_metadata_write"
    LONG_TIMEOUT = "long_timeout"
    SENSITIVE_ENV = "sensitive_env"
    SANDBOX_ENV_OVERRIDE = "sandbox_env_override"
    SHELL_EXPANSION = "shell_expansion"
    INLINE_SCRIPT = "inline_script"
    PRIVILEGED_EXECUTABLE = "privileged_executable"
    WRITE_GENERATED_OR_IGNORED = "write_generated_or_ignored"


PERMISSION_MODES = ("safe", "trusted", "dangerous")
ELICITABLE_PERMISSIONS = frozenset(
    permission.value for permission in OperationPermission
)


@dataclass(frozen=True, slots=True)
class PermissionProfile:
    name: str
    capabilities: frozenset[Capability]
    auto_granted_operations: frozenset[OperationPermission] = frozenset()


_BASE_CAPABILITIES = frozenset(Capability)

_PROFILES = {
    "safe": PermissionProfile(
        name="safe",
        capabilities=_BASE_CAPABILITIES,
    ),
    "trusted": PermissionProfile(
        name="trusted",
        capabilities=_BASE_CAPABILITIES,
        auto_granted_operations=frozenset({OperationPermission.NETWORK}),
    ),
    "dangerous": PermissionProfile(
        name="dangerous",
        capabilities=_BASE_CAPABILITIES,
        auto_granted_operations=frozenset(OperationPermission),
    ),
}


def permission_profile(name: str) -> PermissionProfile:
    try:
        return _PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown permission profile: {name}") from exc

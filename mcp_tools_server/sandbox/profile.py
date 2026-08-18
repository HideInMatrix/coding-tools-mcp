from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .backend import SandboxBackendState


SAFE_CAPABILITIES = (
    "filesystem.read.workspace",
    "filesystem.write.workspace",
    "filesystem.read.toolchains",
    "filesystem.write.runtime",
    "process.execute",
)

TRUSTED_CAPABILITIES = (*SAFE_CAPABILITIES, "network")

DANGEROUS_CAPABILITIES = (
    *TRUSTED_CAPABILITIES,
    "filesystem.read.external",
    "filesystem.write.external",
    "environment.secrets",
    "shell.unrestricted",
)


@dataclass(frozen=True, slots=True)
class SandboxProfile:
    mode: str
    capabilities: tuple[str, ...]
    workspace_rw: tuple[str, ...]
    runtime_rw: tuple[str, ...]
    toolchain_ro: tuple[str, ...]
    protected_ro: tuple[str, ...]
    network: bool
    inherit_secrets: bool
    shell_unrestricted: bool
    backend: str
    os_kernel_sandbox: bool
    process_isolation: bool
    filesystem_isolation: bool
    network_isolation: bool
    backend_available: bool
    backend_reason: str
    experimental_appcontainer_available: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "capabilities": list(self.capabilities),
            "workspace_rw": list(self.workspace_rw),
            "runtime_rw": list(self.runtime_rw),
            "toolchain_ro": list(self.toolchain_ro),
            "protected_ro": list(self.protected_ro),
            "network": self.network,
            "inherit_secrets": self.inherit_secrets,
            "shell_unrestricted": self.shell_unrestricted,
            "backend": self.backend,
            "os_kernel_sandbox": self.os_kernel_sandbox,
            "process_isolation": self.process_isolation,
            "filesystem_isolation": self.filesystem_isolation,
            "network_isolation": self.network_isolation,
            "backend_available": self.backend_available,
            "backend_reason": self.backend_reason,
            "experimental_appcontainer_available": self.experimental_appcontainer_available,
        }


def build_sandbox_profile(
    *,
    mode: str,
    workspace: Path,
    runtime_paths: list[Path],
    toolchain_paths: list[str],
    protected_paths: list[Path],
    network: bool,
    backend: SandboxBackendState,
) -> SandboxProfile:
    if mode == "dangerous":
        capabilities = DANGEROUS_CAPABILITIES
    elif mode == "trusted":
        capabilities = TRUSTED_CAPABILITIES
    else:
        capabilities = SAFE_CAPABILITIES
    return SandboxProfile(
        mode=mode,
        capabilities=capabilities,
        workspace_rw=(str(workspace.resolve()),),
        runtime_rw=tuple(str(path.resolve()) for path in runtime_paths),
        toolchain_ro=tuple(toolchain_paths),
        protected_ro=tuple(str(path.resolve()) for path in protected_paths),
        network=network,
        inherit_secrets=mode == "dangerous",
        shell_unrestricted=mode == "dangerous",
        backend=backend.name,
        os_kernel_sandbox=backend.enabled,
        process_isolation=backend.process_isolation,
        filesystem_isolation=backend.filesystem_isolation,
        network_isolation=backend.network_isolation,
        backend_available=backend.available,
        backend_reason=backend.reason,
        experimental_appcontainer_available=backend.experimental_appcontainer_available,
    )

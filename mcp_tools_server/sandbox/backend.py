from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .windows_launcher import INTERNAL_WINDOWS_SANDBOX_FLAG


@dataclass(frozen=True, slots=True)
class SandboxBackendState:
    name: str
    available: bool
    enabled: bool
    reason: str
    process_isolation: bool = False
    filesystem_isolation: bool = False
    network_isolation: bool = False
    experimental_appcontainer_available: bool = False


class ProcessSandboxBackend:
    def __init__(self, state: SandboxBackendState) -> None:
        self.state = state

    def wrap(
        self,
        argv: list[str],
        *,
        cwd: Path,
        permissions: frozenset[str] = frozenset(),
        readable_roots: tuple[Path, ...] = (),
    ) -> list[str]:
        return list(argv)


class MacSeatbeltBackend(ProcessSandboxBackend):
    EXECUTABLE = Path("/usr/bin/sandbox-exec")

    def __init__(
        self,
        *,
        runtime_dir: Path,
        workspace: Path,
        readable_roots: list[Path],
        writable_roots: list[Path],
        protected_paths: list[Path],
        network: bool,
        enabled: bool,
    ) -> None:
        available = self.EXECUTABLE.is_file() and os.access(self.EXECUTABLE, os.X_OK)
        state = SandboxBackendState(
            name="macos-seatbelt",
            available=available,
            enabled=bool(enabled and available),
            reason=(
                "macOS Seatbelt is enforced with /usr/bin/sandbox-exec"
                if enabled and available
                else "macOS Seatbelt backend is unavailable or disabled"
            ),
            process_isolation=bool(enabled and available),
            filesystem_isolation=bool(enabled and available),
            network_isolation=bool(enabled and available),
        )
        super().__init__(state)
        self.runtime_dir = runtime_dir.resolve()
        self.workspace = workspace.resolve()
        self.readable_roots = list(readable_roots)
        self.writable_roots = list(writable_roots)
        self.protected_paths = list(protected_paths)
        self.network = network
        self.profile_path = runtime_dir / "seatbelt.sbpl"
        if self.state.enabled:
            self.profile_path.write_text(
                self._profile(
                    workspace=workspace,
                    readable_roots=readable_roots,
                    writable_roots=writable_roots,
                    protected_paths=protected_paths,
                    network=network,
                ),
                encoding="utf-8",
            )

    @staticmethod
    def _quoted(path: Path) -> str:
        value = str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{value}"'

    def _profile(
        self,
        *,
        workspace: Path,
        readable_roots: list[Path],
        writable_roots: list[Path],
        protected_paths: list[Path],
        network: bool,
    ) -> str:
        # Closed by default. Keep the policy intentionally smaller than a
        # desktop-app sandbox: CLI children need process/sysctl/mach services,
        # but filesystem access is only granted to system/runtime/toolchain
        # roots and the configured workspace.
        components = [
            "(version 1)",
            "(deny default)",
            "(allow process-exec)",
            "(allow process-fork)",
            "(allow signal (target same-sandbox))",
            "(allow process-info*)",
            "(allow sysctl-read)",
            "(allow user-preference-read)",
            "(allow pseudo-tty)",
            '(allow system-mac-syscall (mac-policy-name "vnguard"))',
            '(allow system-mac-syscall (require-all (mac-policy-name "Sandbox") (mac-syscall-number 67)))',
            '(allow file-read* file-test-existence (literal "/"))',
            '(allow file-read-metadata file-test-existence (literal "/etc") (literal "/tmp") (literal "/var") (literal "/private/etc/localtime"))',
            '(allow file-read-metadata file-test-existence (path-ancestors "/System/Volumes/Data/private"))',
            '(allow file-map-executable (subpath "/System/Library/Frameworks") (subpath "/System/Library/PrivateFrameworks") (subpath "/usr/lib"))',
            '(allow file-read* file-test-existence (literal "/dev/random") (literal "/dev/urandom") (literal "/private/etc/passwd") (literal "/private/etc/services"))',
            '(allow file-read* file-test-existence file-write-data (literal "/dev/null") (literal "/dev/zero"))',
            '(allow file-read-data file-test-existence file-write-data (subpath "/dev/fd"))',
            '(allow file-read* file-write* (literal "/dev/ptmx") (literal "/dev/tty"))',
            '(allow file-read-metadata (literal "/dev") (regex "^/dev/.*$"))',
            '(allow file-read* file-write* (regex "^/dev/ttys[0-9]+$"))',
            '(allow file-ioctl (regex "^/dev/ttys[0-9]+$"))',
            '(allow iokit-open (iokit-registry-entry-class "RootDomainUserClient"))',
            '(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo") (global-name "com.apple.system.opendirectoryd.membership") (global-name "com.apple.cfprefsd.agent") (global-name "com.apple.cfprefsd.daemon") (global-name "com.apple.bsd.dirhelper") (global-name "com.apple.logd"))',
        ]
        roots: list[Path] = [workspace, *readable_roots, *writable_roots]
        seen: set[str] = set()
        for root in roots:
            try:
                resolved = root.resolve()
            except OSError:
                continue
            key = str(resolved)
            if key in seen or not resolved.exists():
                continue
            seen.add(key)
            quoted = self._quoted(resolved)
            components.append(f"(allow file-read* file-test-existence (subpath {quoted}))")
            components.append(f"(allow file-map-executable (subpath {quoted}))")
            components.append(f"(allow file-read-metadata file-test-existence (path-ancestors {quoted}))")
        seen.clear()
        for root in [workspace, *writable_roots]:
            try:
                resolved = root.resolve()
            except OSError:
                continue
            key = str(resolved)
            if key in seen or not resolved.exists():
                continue
            seen.add(key)
            components.append(f"(allow file-write* (subpath {self._quoted(resolved)}))")
        for protected in protected_paths:
            try:
                resolved = protected.resolve()
            except OSError:
                continue
            if not resolved.exists():
                continue
            qualifier = "subpath" if resolved.is_dir() else "literal"
            components.append(
                f"(deny file-write* ({qualifier} {self._quoted(resolved)}))"
            )
        if network:
            components.extend(
                [
                    "(allow network-outbound)",
                    "(allow network-inbound)",
                    "(allow network-bind)",
                    '(allow system-socket (require-all (socket-domain AF_SYSTEM) (socket-protocol 2)))',
                    '(allow mach-lookup (global-name "com.apple.SecurityServer") (global-name "com.apple.networkd") (global-name "com.apple.ocspd") (global-name "com.apple.trustd") (global-name "com.apple.trustd.agent") (global-name "com.apple.SystemConfiguration.DNSConfiguration") (global-name "com.apple.SystemConfiguration.configd"))',
                ]
            )
        return "\n".join(components) + "\n"

    def _profile_path_for_permissions(
        self,
        permissions: frozenset[str],
        readable_roots: tuple[Path, ...] = (),
    ) -> Path:
        network = self.network or "network" in permissions
        allow_protected_write = "git_metadata_write" in permissions
        if network == self.network and not allow_protected_write and not readable_roots:
            return self.profile_path
        roots_key = hashlib.sha256(
            "\0".join(sorted(str(path.resolve()) for path in readable_roots)).encode("utf-8")
        ).hexdigest()[:12]
        suffix = (
            f"network-{int(network)}-git-write-{int(allow_protected_write)}-read-{roots_key}"
        )
        path = self.runtime_dir / f"seatbelt-{suffix}.sbpl"
        if not path.exists():
            path.write_text(
                self._profile(
                    workspace=self.workspace,
                    readable_roots=[*self.readable_roots, *readable_roots],
                    writable_roots=self.writable_roots,
                    protected_paths=(
                        [] if allow_protected_write else self.protected_paths
                    ),
                    network=network,
                ),
                encoding="utf-8",
            )
        return path

    def wrap(
        self,
        argv: list[str],
        *,
        cwd: Path,
        permissions: frozenset[str] = frozenset(),
        readable_roots: tuple[Path, ...] = (),
    ) -> list[str]:
        if not self.state.enabled:
            return list(argv)
        profile_path = self._profile_path_for_permissions(permissions, readable_roots)
        return [str(self.EXECUTABLE), "-f", str(profile_path), *argv]


class LinuxBubblewrapBackend(ProcessSandboxBackend):
    def __init__(
        self,
        *,
        workspace: Path,
        readable_roots: list[Path],
        writable_roots: list[Path],
        protected_paths: list[Path],
        network: bool,
        enabled: bool,
    ) -> None:
        executable = self._find_bwrap(workspace)
        self.executable = executable
        available = executable is not None
        state = SandboxBackendState(
            name="linux-bubblewrap",
            available=available,
            enabled=bool(enabled and available),
            reason=(
                f"bubblewrap is enforced via {executable}"
                if enabled and executable
                else "bubblewrap is unavailable or disabled"
            ),
            process_isolation=bool(enabled and available),
            filesystem_isolation=bool(enabled and available),
            network_isolation=bool(enabled and available),
        )
        super().__init__(state)
        self.workspace = workspace.resolve()
        self.readable_roots = self._unique_existing(readable_roots)
        self.writable_roots = self._unique_existing(writable_roots)
        self.protected_paths = self._unique_existing(protected_paths)
        self.network = network

    @staticmethod
    def _find_bwrap(workspace: Path) -> str | None:
        for candidate in (Path("/usr/bin/bwrap"), Path("/bin/bwrap")):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        resolved = shutil.which("bwrap")
        if not resolved:
            return None
        path = Path(resolved).resolve()
        try:
            path.relative_to(workspace.resolve())
            return None
        except ValueError:
            return str(path)

    @staticmethod
    def _unique_existing(paths: list[Path]) -> list[Path]:
        seen: set[str] = set()
        result: list[Path] = []
        for raw in paths:
            try:
                path = raw.resolve()
            except OSError:
                continue
            key = str(path)
            if key in seen or not path.exists():
                continue
            seen.add(key)
            result.append(path)
        return result

    @staticmethod
    def _mkdir_args(
        path: Path,
        covered_roots: list[Path],
        created: set[str],
    ) -> list[str]:
        args: list[str] = []
        chain = list(reversed(path.parents)) + [path]
        for item in chain:
            if str(item) == "/":
                continue
            if any(
                item == root
                or (root != Path("/tmp") and item.is_relative_to(root))
                for root in covered_roots
            ):
                continue
            key = str(item)
            if key in created:
                continue
            created.add(key)
            args.extend(["--dir", str(item)])
        return args

    def wrap(
        self,
        argv: list[str],
        *,
        cwd: Path,
        permissions: frozenset[str] = frozenset(),
        readable_roots: tuple[Path, ...] = (),
    ) -> list[str]:
        if not self.state.enabled or self.executable is None:
            return list(argv)
        args = [
            self.executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
        ]
        if not (self.network or "network" in permissions):
            args.append("--unshare-net")

        system_roots: list[Path] = []
        for raw in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/run"):
            path = Path(raw)
            if path.exists():
                system_roots.append(path.resolve())
                args.extend(["--ro-bind", raw, raw])
        args.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])
        covered_roots = [*system_roots, Path("/tmp")]
        created: set[str] = set()

        bound: set[str] = set()
        for root in [*self.readable_roots, *self._unique_existing(list(readable_roots))]:
            if any(root == system or root.is_relative_to(system) for system in system_roots):
                continue
            key = str(root)
            if key in bound:
                continue
            args.extend(self._mkdir_args(root, covered_roots, created))
            args.extend(["--ro-bind", key, key])
            bound.add(key)
        for root in [self.workspace, *self.writable_roots]:
            key = str(root)
            args.extend(self._mkdir_args(root, covered_roots, created))
            args.extend(["--bind", key, key])
            bound.add(key)
        if "git_metadata_write" not in permissions:
            for protected in self.protected_paths:
                key = str(protected)
                args.extend(self._mkdir_args(protected, covered_roots, created))
                args.extend(["--ro-bind", key, key])
        args.extend(["--chdir", str(cwd.resolve()), "--", *argv])
        return args


class WindowsRestrictedTokenBackend(ProcessSandboxBackend):
    def __init__(self, *, enabled: bool) -> None:
        available = self._restricted_token_available()
        experimental = self._experimental_appcontainer_available()
        active = bool(enabled and available)
        reason = (
            "Windows Restricted Token + Job Object process isolation is enforced; "
            "filesystem and network confinement still rely on application policy"
            if active
            else "Windows Restricted Token backend is unavailable or disabled"
        )
        if experimental:
            reason += "; Experimental_CreateProcessInSandbox is present but not used by default"
        super().__init__(
            SandboxBackendState(
                name="windows-restricted-token",
                available=available,
                enabled=active,
                reason=reason,
                process_isolation=active,
                filesystem_isolation=False,
                network_isolation=False,
                experimental_appcontainer_available=experimental,
            )
        )

    @staticmethod
    def _restricted_token_available() -> bool:
        if os.name != "nt":
            return False
        try:
            import ctypes

            advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            required_advapi = (
                "OpenProcessToken",
                "CreateRestrictedToken",
                "CreateProcessAsUserW",
                "CreateProcessWithTokenW",
                "CreateWellKnownSid",
            )
            required_kernel = (
                "CreateJobObjectW",
                "SetInformationJobObject",
                "AssignProcessToJobObject",
            )
            return all(hasattr(advapi32, name) for name in required_advapi) and all(
                hasattr(kernel32, name) for name in required_kernel
            )
        except (ImportError, OSError):
            return False

    @staticmethod
    def _experimental_appcontainer_available() -> bool:
        if os.name != "nt":
            return False
        try:
            import ctypes

            module = ctypes.WinDLL("processmodel.dll", use_last_error=True)
            return hasattr(module, "Experimental_CreateProcessInSandbox")
        except (ImportError, OSError):
            return False

    @staticmethod
    def _launcher_prefix() -> list[str]:
        if bool(getattr(sys, "frozen", False)):
            return [sys.executable, INTERNAL_WINDOWS_SANDBOX_FLAG]
        launcher = Path(__file__).with_name("windows_launcher.py").resolve()
        return [sys.executable, str(launcher)]

    def wrap(
        self,
        argv: list[str],
        *,
        cwd: Path,
        permissions: frozenset[str] = frozenset(),
        readable_roots: tuple[Path, ...] = (),
    ) -> list[str]:
        if not self.state.enabled:
            return list(argv)
        return [*self._launcher_prefix(), "--", *argv]


def _probe_backend(
    backend: ProcessSandboxBackend,
    *,
    workspace: Path,
) -> tuple[bool, str]:
    if not backend.state.enabled:
        return (False, backend.state.reason)
    system = platform.system().lower()
    if system == "darwin":
        probe = "/usr/bin/true"
    elif system == "linux":
        probe = "/usr/bin/true" if Path("/usr/bin/true").is_file() else "/bin/true"
    elif system == "windows":
        comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
        probe_argv = [comspec, "/d", "/s", "/c", "exit 0"]
    else:
        return (False, "no OS sandbox probe is available for this platform")
    if system != "windows":
        probe_argv = [probe]
    if system == "windows":
        probe_env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "TEMP",
                "TMP",
                "PATHEXT",
            }
        }
    else:
        probe_env = {"PATH": "/usr/bin:/bin", "HOME": str(workspace)}
    try:
        completed = subprocess.run(
            backend.wrap(probe_argv, cwd=workspace),
            cwd=str(workspace),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
            shell=False,
            env=probe_env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, f"OS sandbox probe failed: {exc}")
    if completed.returncode != 0:
        detail = completed.stderr.strip()[:400]
        return (
            False,
            f"OS sandbox probe exited with {completed.returncode}"
            + (f": {detail}" if detail else ""),
        )
    return (True, backend.state.reason)


def create_process_sandbox(
    *,
    mode: str,
    workspace: Path,
    runtime_dir: Path,
    readable_roots: list[Path],
    writable_roots: list[Path],
    protected_paths: list[Path],
    network: bool,
) -> ProcessSandboxBackend:
    preference = os.environ.get("CODING_TOOLS_MCP_OS_SANDBOX", "auto").strip().lower()
    if preference not in {"auto", "off", "require"}:
        preference = "auto"
    if mode == "dangerous" or preference == "off":
        return ProcessSandboxBackend(
            SandboxBackendState(
                name="application-policy",
                available=False,
                enabled=False,
                reason="OS sandbox is bypassed by configuration or dangerous mode",
            )
        )

    enabled = preference in {"auto", "require"}
    system = platform.system().lower()
    if system == "darwin":
        backend: ProcessSandboxBackend = MacSeatbeltBackend(
            runtime_dir=runtime_dir,
            workspace=workspace,
            readable_roots=readable_roots,
            writable_roots=writable_roots,
            protected_paths=protected_paths,
            network=network,
            enabled=enabled,
        )
    elif system == "linux":
        backend = LinuxBubblewrapBackend(
            workspace=workspace,
            readable_roots=readable_roots,
            writable_roots=writable_roots,
            protected_paths=protected_paths,
            network=network,
            enabled=enabled,
        )
    elif system == "windows":
        backend = WindowsRestrictedTokenBackend(enabled=enabled)
    else:
        backend = ProcessSandboxBackend(
            SandboxBackendState(
                name="application-policy",
                available=False,
                enabled=False,
                reason=f"no OS process sandbox backend is implemented for {platform.system()}",
            )
        )
    if backend.state.enabled:
        passed, reason = _probe_backend(backend, workspace=workspace)
        if not passed:
            if preference == "require":
                raise RuntimeError(
                    f"OS sandbox is required but failed its probe: {reason}"
                )
            backend.state = SandboxBackendState(
                name=backend.state.name,
                available=backend.state.available,
                enabled=False,
                reason=f"OS sandbox disabled after failed probe: {reason}",
                process_isolation=False,
                filesystem_isolation=False,
                network_isolation=False,
                experimental_appcontainer_available=backend.state.experimental_appcontainer_available,
            )
    if preference == "require" and not backend.state.enabled:
        raise RuntimeError(f"OS sandbox is required but unavailable: {backend.state.reason}")
    return backend

from __future__ import annotations

import json
import os
import platform
import re
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .models import ToolchainCandidate


_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")
_PROGRAM_RE = re.compile(r"^[A-Za-z0-9_.+@-]+$")
_SENSITIVE_ENV_RE = re.compile(
    r"(token|secret|credential|api[_-]?key|password|passwd|private)", re.I
)
_PROGRAM_KINDS = {
    "node": "node", "npm": "node", "npx": "node", "corepack": "node",
    "pnpm": "node", "yarn": "node", "python": "python",
    "python3": "python", "pip": "python", "pip3": "python", "go": "go",
    "gofmt": "go",
}

ProbeRunner = Callable[
    [list[str], Mapping[str, str], float, bool, Sequence[Path]],
    subprocess.CompletedProcess[str],
]


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _version_key(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.search(value)
    if match is None:
        return (0, 0, 0)
    parts = [int(item or 0) for item in match.groups()]
    return tuple(parts)  # type: ignore[return-value]


def _normalize_version(value: str) -> str:
    match = _VERSION_RE.search(value.strip())
    if match is None:
        return value.strip().lstrip("v")
    raw_groups = match.groups()
    parts = [str(int(raw_groups[0]))]
    if raw_groups[1] is not None:
        parts.append(str(int(raw_groups[1])))
    if raw_groups[2] is not None:
        parts.append(str(int(raw_groups[2])))
    return ".".join(parts)


class ToolchainResolver:
    """Resolve tools by querying an execution environment instead of scanning Home.

    Runtime injects a probe runner which applies the active OS sandbox. Normal
    discovery therefore sees exactly the PATH/filesystem available to sandboxed
    commands. A privileged retry is explicit and is only used after the caller
    has obtained the ``privileged_executable`` permission.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        home: Path | None = None,
        safe_path: Sequence[str] | None = None,
        probe_runner: ProbeRunner | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.home = (home or Path.home()).expanduser().resolve()
        self._safe_path = self._normalize_path_entries(
            safe_path if safe_path is not None else self.default_search_path(self.workspace)
        )
        self._probe_runner = probe_runner or self._run_probe_direct
        self._cache: dict[tuple[str, bool], dict[str, object]] = {}
        self._program_cache: dict[tuple[str, bool], Path | None] = {}

    @classmethod
    def default_search_path(cls, workspace: Path) -> list[str]:
        """Return platform-controlled PATH entries safe to present to a sandbox."""

        root = workspace.resolve()
        raw_entries = (
            os.environ.get("PATH", "").split(os.pathsep)
            if os.name == "nt"
            else []
        )
        raw_entries.extend(cls.system_path_entries())
        result: list[str] = []
        seen: set[str] = set()
        for raw in raw_entries:
            if not raw:
                continue
            try:
                path = Path(raw).expanduser().resolve()
                mode = path.stat().st_mode
            except OSError:
                continue
            key = os.path.normcase(str(path))
            if key in seen or not path.is_dir() or _within(path, root):
                continue
            if mode & stat.S_IWOTH:
                continue
            seen.add(key)
            result.append(str(path))
        return result

    @staticmethod
    def system_path_entries() -> list[str]:
        if os.name == "nt":
            root = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
            return [str(root / "System32"), str(root)]
        if platform.system().lower() == "darwin":
            return [
                "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
                "/usr/sbin", "/sbin",
            ]
        return [
            "/usr/local/bin", "/usr/bin", "/bin", "/usr/local/sbin",
            "/usr/sbin", "/sbin",
        ]

    def discover(
        self,
        kinds: list[str] | None = None,
        *,
        privileged: bool = False,
    ) -> dict[str, object]:
        requested = kinds or ["node", "python", "go"]
        result: dict[str, object] = {}
        for raw_kind in requested:
            kind = raw_kind.strip().lower()
            if kind in {"node", "python", "go"}:
                result[kind] = self._discover_kind(kind, privileged=privileged)
        return {
            "toolchains": result,
            "safe_path": self.safe_path_entries(result),
            "privileged_lookup": privileged,
        }

    def selected(self, kind: str, *, privileged: bool = False) -> ToolchainCandidate | None:
        payload = self._discover_kind(kind, privileged=privileged)
        selected = payload.get("selected")
        if not isinstance(selected, dict):
            return None
        return ToolchainCandidate(
            kind=str(selected["kind"]),
            version=str(selected["version"]),
            source=str(selected["source"]),
            root=Path(str(selected["root"])),
            bin_dir=Path(str(selected["bin_dir"])),
            executables={str(k): str(v) for k, v in dict(selected["executables"]).items()},
            selected_reason=str(selected.get("selected_reason") or ""),
        )

    def safe_path_entries(self, discovered: dict[str, object] | None = None) -> list[str]:
        payload = discovered or {
            kind: self._discover_kind(kind, privileged=False)
            for kind in ("node", "python", "go")
        }
        entries: list[str] = []
        for value in payload.values():
            if not isinstance(value, dict):
                continue
            selected = value.get("selected")
            if isinstance(selected, dict):
                entries.append(str(selected.get("bin_dir") or ""))
        entries.extend(self._safe_path)
        return self._normalize_path_entries(entries)

    def resolve_program(self, name: str, *, privileged: bool = False) -> str | None:
        raw = name.strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        if path.is_absolute():
            resolved = self._validated_executable(path)
            if resolved is None:
                return None
            safe_dirs = {os.path.normcase(item) for item in self.safe_path_entries()}
            if (
                privileged
                or _within(resolved, self.workspace)
                or os.path.normcase(str(path.parent.resolve())) in safe_dirs
            ):
                return str(resolved)
            return None

        kind = _PROGRAM_KINDS.get(raw)
        if kind is not None:
            payload = self._discover_kind(kind, privileged=privileged)
            selected = payload.get("selected")
            if isinstance(selected, dict):
                executables = selected.get("executables")
                if isinstance(executables, dict):
                    candidate = executables.get(raw)
                    if isinstance(candidate, str) and self._validated_executable(Path(candidate)):
                        return candidate

        safe = self._query_program(raw, privileged=False)
        if safe is not None or not privileged:
            return str(safe) if safe is not None else None
        elevated = self._query_program(raw, privileged=True)
        return str(elevated) if elevated is not None else None

    def readable_root_for_program(
        self,
        name: str,
        resolved: str,
        *,
        privileged: bool,
    ) -> Path:
        """Return the narrow read-only root required to execute a resolved tool."""

        kind = _PROGRAM_KINDS.get(name.strip())
        if kind is not None:
            payload = self._discover_kind(kind, privileged=privileged)
            selected = payload.get("selected")
            if isinstance(selected, dict):
                executables = selected.get("executables")
                if isinstance(executables, dict) and resolved in executables.values():
                    return Path(str(selected["root"])).resolve()
        return Path(resolved).resolve().parent

    def _discover_kind(self, kind: str, *, privileged: bool) -> dict[str, object]:
        safe = self._discover_scope(kind, privileged=False)
        if safe.get("selected") is not None or not privileged:
            return safe
        return self._discover_scope(kind, privileged=True)

    def _discover_scope(self, kind: str, *, privileged: bool) -> dict[str, object]:
        cache_key = (kind, privileged)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        hint = self._workspace_hint(kind)
        candidates = self._candidates(kind, privileged=privileged)
        selected: ToolchainCandidate | None = None
        if hint:
            normalized_hint = _normalize_version(hint)
            matches = [item for item in candidates if item.version.startswith(normalized_hint)]
            if matches:
                chosen = max(matches, key=lambda item: _version_key(item.version))
                selected = self._selected_candidate(
                    chosen,
                    f"workspace hint {hint} ({'elevated' if privileged else 'sandbox'} PATH)",
                )
        if selected is None and candidates:
            selected = self._selected_candidate(
                candidates[0],
                "elevated user environment" if privileged else "sandbox PATH",
            )
        payload = {
            "hint": hint,
            "selected": selected.to_dict() if selected else None,
            "candidates": [item.to_dict() for item in candidates],
            "lookup_scope": "elevated" if privileged else "sandbox",
        }
        self._cache[cache_key] = payload
        return payload

    @staticmethod
    def _selected_candidate(candidate: ToolchainCandidate, reason: str) -> ToolchainCandidate:
        return ToolchainCandidate(
            kind=candidate.kind,
            version=candidate.version,
            source=candidate.source,
            root=candidate.root,
            bin_dir=candidate.bin_dir,
            executables=dict(candidate.executables),
            selected_reason=reason,
        )

    def _workspace_hint(self, kind: str) -> str:
        if kind == "node":
            for name in (".nvmrc", ".node-version"):
                value = self._read_hint(name)
                if value:
                    return value
            try:
                payload = json.loads((self.workspace / "package.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            engines = payload.get("engines") if isinstance(payload, dict) else None
            if isinstance(engines, dict):
                raw = engines.get("node")
                if isinstance(raw, str) and re.fullmatch(r"v?\d+(?:\.\d+){0,2}", raw.strip()):
                    return raw.strip()
            return ""
        if kind == "python":
            return self._read_hint(".python-version")
        if kind == "go":
            direct = self._read_hint(".go-version")
            if direct:
                return direct
            try:
                for line in (self.workspace / "go.mod").read_text(encoding="utf-8").splitlines():
                    if line.startswith("go "):
                        return line.split(None, 1)[1].strip()
            except OSError:
                pass
        return ""

    def _read_hint(self, filename: str) -> str:
        try:
            return (self.workspace / filename).read_text(encoding="utf-8").strip().splitlines()[0]
        except (OSError, IndexError):
            return ""

    def _candidates(self, kind: str, *, privileged: bool) -> list[ToolchainCandidate]:
        primary_names = {
            "node": ["node"], "python": ["python3", "python"], "go": ["go"],
        }[kind]
        executable_names = {
            "node": ["node", "npm", "npx", "corepack", "pnpm", "yarn"],
            "python": ["python", "python3", "pip", "pip3"],
            "go": ["go", "gofmt"],
        }[kind]
        paths: list[tuple[Path, str, Path]] = []
        for name in primary_names:
            executable = self._query_program(name, privileged=privileged)
            if executable is not None:
                paths.append((
                    executable,
                    "elevated_path" if privileged else "sandbox_path",
                    executable.parent.parent,
                ))
        return self._build_candidates(
            kind, paths, executable_names, privileged=privileged,
        )

    def _build_candidates(
        self,
        kind: str,
        paths: list[tuple[Path, str, Path]],
        executable_names: list[str],
        *,
        privileged: bool,
    ) -> list[ToolchainCandidate]:
        seen: set[str] = set()
        result: list[ToolchainCandidate] = []
        for candidate, source, root in paths:
            resolved = self._validated_executable(candidate)
            if resolved is None:
                continue
            try:
                bin_dir = candidate.parent.resolve()
                resolved_root = root.expanduser().resolve()
            except OSError:
                continue
            key = os.path.normcase(str(resolved))
            if key in seen or not self._trusted_executable(resolved, bin_dir, resolved_root):
                continue
            seen.add(key)
            version = self._read_version(
                kind, resolved, bin_dir, root=resolved_root, privileged=privileged,
            )
            if not version:
                continue
            executables: dict[str, str] = {}
            for name in executable_names:
                for filename in self._executable_filenames(name):
                    validated = self._validated_executable(bin_dir / filename)
                    if validated is not None:
                        executables[name] = str(validated)
                        break
            result.append(ToolchainCandidate(
                kind=kind,
                version=version,
                source=source,
                root=resolved_root,
                bin_dir=bin_dir,
                executables=executables,
            ))
        return result

    def _query_program(self, name: str, *, privileged: bool) -> Path | None:
        if not _PROGRAM_RE.fullmatch(name):
            return None
        cache_key = (name, privileged)
        if cache_key in self._program_cache:
            return self._program_cache[cache_key]
        env = self._probe_env(privileged=privileged)
        if os.name == "nt":
            system_root = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
            argv = [str(system_root / "System32" / "where.exe"), name]
        elif privileged:
            shell = self._login_shell()
            if shell.name.lower() in {"fish", "fish.exe"}:
                argv = [str(shell), "-ilc", 'command -v "$argv[1]"', name]
            else:
                argv = [
                    str(shell), "-ilc",
                    'candidate=$(command -v "$1") && case "$candidate" in /*) printf "%s\\n" "$candidate";; *) exit 1;; esac',
                    "agent-runtime", name,
                ]
        else:
            argv = [
                "/bin/sh", "-c",
                'candidate=$(command -v "$1") && case "$candidate" in /*) printf "%s\\n" "$candidate";; *) exit 1;; esac',
                "agent-runtime", name,
            ]
        try:
            completed = self._probe_runner(
                argv, env, 5.0, privileged, [self.home] if privileged else [],
            )
        except (OSError, subprocess.SubprocessError):
            self._program_cache[cache_key] = None
            return None
        if completed.returncode != 0:
            self._program_cache[cache_key] = None
            return None
        for raw_line in reversed(completed.stdout.splitlines()):
            raw = raw_line.strip().strip('"')
            if raw:
                path = Path(raw).expanduser()
                if path.is_absolute():
                    validated = self._validated_executable(path)
                    if validated is not None:
                        self._program_cache[cache_key] = validated
                        return validated
        self._program_cache[cache_key] = None
        return None

    def _read_version(
        self,
        kind: str,
        executable: Path,
        bin_dir: Path,
        *,
        root: Path,
        privileged: bool,
    ) -> str:
        args = [str(executable), "--version"] if kind != "go" else [str(executable), "version"]
        env = self._probe_env(privileged=privileged)
        env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
        try:
            completed = self._probe_runner(args, env, 3.0, privileged, [root])
        except (OSError, subprocess.SubprocessError):
            return ""
        if completed.returncode != 0:
            return ""
        return _normalize_version(completed.stdout)

    def _probe_env(self, *, privileged: bool) -> dict[str, str]:
        if privileged:
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("AGENT_RUNTIME_")
                and not _SENSITIVE_ENV_RE.search(key)
            }
            env["HOME"] = str(self.home)
        else:
            env = {
                key: value for key, value in os.environ.items()
                if key.upper() in {
                    "LANG", "LC_ALL", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
                }
            }
            env["HOME"] = str(self.workspace)
            env["PATH"] = os.pathsep.join(self._safe_path)
        env.setdefault("LANG", "C.UTF-8")
        return env

    def _login_shell(self) -> Path:
        raw = os.environ.get("SHELL", "").strip()
        candidates = [Path(raw)] if raw else []
        candidates.extend([Path("/bin/zsh"), Path("/bin/bash"), Path("/bin/sh")])
        for candidate in candidates:
            validated = self._validated_executable(candidate)
            if validated is not None:
                return validated
        return Path("/bin/sh")

    @staticmethod
    def _run_probe_direct(
        argv: list[str],
        env: Mapping[str, str],
        timeout: float,
        _privileged: bool,
        _readable_roots: Sequence[Path],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            shell=False,
            env=dict(env),
        )

    @staticmethod
    def _trusted_executable(executable: Path, bin_dir: Path, root: Path) -> bool:
        try:
            invocation_dir = executable.parent.resolve()
            target = executable.resolve()
            if not _within(invocation_dir, root) and invocation_dir != bin_dir:
                return False
            if not _within(target, root) and not _within(target, bin_dir):
                return False
            return not any(
                path.stat().st_mode & stat.S_IWOTH
                for path in (target, invocation_dir)
            )
        except OSError:
            return False

    @staticmethod
    def _validated_executable(path: Path) -> Path | None:
        try:
            candidate = path.expanduser()
            if not candidate.is_absolute():
                candidate = Path(os.path.abspath(candidate))
            if not candidate.is_file() or (os.name != "nt" and not os.access(candidate, os.X_OK)):
                return None
            # Validate the real target for safety, but preserve the invocation
            # path. Tool managers commonly expose multi-call shims/symlinks
            # (pnpm -> manager binary, node -> manager binary, etc.) whose
            # basename/argv[0] selects the actual tool. Executing the resolved
            # target would silently turn `pnpm build` into `manager build`.
            target = candidate.resolve()
            if target.stat().st_mode & stat.S_IWOTH:
                return None
            return candidate
        except OSError:
            return None

    @staticmethod
    def _normalize_path_entries(entries: Sequence[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in entries:
            if not raw:
                continue
            try:
                path = Path(raw).expanduser().resolve()
            except OSError:
                continue
            key = os.path.normcase(str(path))
            if key not in seen and path.is_dir():
                seen.add(key)
                result.append(str(path))
        return result

    @staticmethod
    def _executable_filenames(name: str) -> list[str]:
        if os.name == "nt":
            return [f"{name}.exe", f"{name}.cmd", f"{name}.bat", name]
        return [name]

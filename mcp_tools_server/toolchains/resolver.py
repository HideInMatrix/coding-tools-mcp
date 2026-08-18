from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import subprocess
from pathlib import Path

from .models import ToolchainCandidate


_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


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
    """Discover developer toolchains without evaluating shell startup files.

    The resolver only inspects deterministic known locations, the current PATH,
    and version hints inside the configured workspace. It never sources
    ``.zshrc``/``.profile`` and never recursively scans the user's home.
    """

    def __init__(self, workspace: Path, *, home: Path | None = None) -> None:
        self.workspace = workspace.resolve()
        self.home = (home or Path.home()).expanduser().resolve()
        self._cache: dict[str, dict[str, object]] = {}

    def discover(self, kinds: list[str] | None = None) -> dict[str, object]:
        requested = kinds or ["node", "python", "go"]
        result: dict[str, object] = {}
        for raw_kind in requested:
            kind = raw_kind.strip().lower()
            if kind not in {"node", "python", "go"}:
                continue
            result[kind] = self._discover_kind(kind)
        return {"toolchains": result, "safe_path": self.safe_path_entries(result)}

    def selected(self, kind: str) -> ToolchainCandidate | None:
        payload = self._discover_kind(kind)
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
            kind: self._discover_kind(kind) for kind in ("node", "python", "go")
        }
        entries: list[Path] = []
        for value in payload.values():
            if not isinstance(value, dict):
                continue
            selected = value.get("selected")
            if isinstance(selected, dict):
                entries.append(Path(str(selected.get("bin_dir") or "")))

        if os.name == "nt":
            system_root = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
            entries.extend([system_root / "System32", system_root])
        else:
            if platform.system().lower() == "darwin":
                entries.extend(
                    [
                        Path("/opt/homebrew/bin"),
                        Path("/usr/local/bin"),
                        Path("/usr/bin"),
                        Path("/bin"),
                        Path("/usr/sbin"),
                        Path("/sbin"),
                    ]
                )
            else:
                entries.extend(
                    [
                        Path("/usr/local/bin"),
                        Path("/usr/bin"),
                        Path("/bin"),
                        Path("/usr/local/sbin"),
                        Path("/usr/sbin"),
                        Path("/sbin"),
                    ]
                )

        seen: set[str] = set()
        result: list[str] = []
        for entry in entries:
            if not str(entry):
                continue
            try:
                resolved = entry.expanduser().resolve()
            except OSError:
                continue
            key = os.path.normcase(str(resolved))
            if key in seen or not resolved.is_dir():
                continue
            seen.add(key)
            result.append(str(resolved))
        return result

    def resolve_program(self, name: str) -> str | None:
        raw = name.strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        if path.is_absolute():
            if not self._executable_file(path):
                return None
            try:
                lexical_parent = path.parent.resolve()
                resolved = path.resolve()
            except OSError:
                return None
            safe_dirs = {os.path.normcase(item) for item in self.safe_path_entries()}
            if _within(resolved, self.workspace) or os.path.normcase(str(lexical_parent)) in safe_dirs:
                return str(path)
            return None
        for kind in ("node", "python", "go"):
            payload = self._discover_kind(kind)
            selected = payload.get("selected")
            if not isinstance(selected, dict):
                continue
            executables = selected.get("executables")
            if not isinstance(executables, dict):
                continue
            resolved = executables.get(raw)
            if isinstance(resolved, str) and self._executable_file(Path(resolved)):
                return resolved
        lookup_path = os.pathsep.join(self.safe_path_entries())
        resolved = shutil.which(raw, path=lookup_path)
        return str(Path(resolved)) if resolved else None

    def _discover_kind(self, kind: str) -> dict[str, object]:
        cached = self._cache.get(kind)
        if cached is not None:
            return cached
        hint = self._workspace_hint(kind)
        candidates = self._candidates(kind)
        selected: ToolchainCandidate | None = None
        if hint:
            normalized_hint = _normalize_version(hint)
            matches = [item for item in candidates if item.version.startswith(normalized_hint)]
            if matches:
                chosen = max(matches, key=lambda item: _version_key(item.version))
                selected = ToolchainCandidate(
                    kind=chosen.kind,
                    version=chosen.version,
                    source=chosen.source,
                    root=chosen.root,
                    bin_dir=chosen.bin_dir,
                    executables=dict(chosen.executables),
                    selected_reason=f"workspace hint {hint}",
                )
        if selected is None and candidates:
            path_candidates = [item for item in candidates if item.source == "path"]
            chosen = path_candidates[0] if path_candidates else max(
                candidates, key=lambda item: _version_key(item.version)
            )
            selected = ToolchainCandidate(
                kind=chosen.kind,
                version=chosen.version,
                source=chosen.source,
                root=chosen.root,
                bin_dir=chosen.bin_dir,
                executables=dict(chosen.executables),
                selected_reason="current PATH" if path_candidates else "highest discovered version",
            )
        payload = {
            "hint": hint,
            "selected": selected.to_dict() if selected else None,
            "candidates": [item.to_dict() for item in candidates],
        }
        self._cache[kind] = payload
        return payload

    def _workspace_hint(self, kind: str) -> str:
        if kind == "node":
            for name in (".nvmrc", ".node-version"):
                value = self._read_hint(name)
                if value:
                    return value
            package_json = self.workspace / "package.json"
            try:
                payload = json.loads(package_json.read_text(encoding="utf-8"))
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

    def _candidates(self, kind: str) -> list[ToolchainCandidate]:
        if kind == "node":
            return self._node_candidates()
        if kind == "python":
            return self._python_candidates()
        return self._go_candidates()

    def _node_candidates(self) -> list[ToolchainCandidate]:
        paths: list[tuple[Path, str, Path]] = []
        current = shutil.which("node")
        if current:
            executable = Path(current)
            paths.append((executable, "path", executable.parent.parent))
        standard_roots = self._standard_roots()
        for root in standard_roots:
            paths.append((root / "bin" / self._exe("node"), "standard", root))
            paths.append((root / self._exe("node"), "standard", root))
        for base in (self.home / ".nvm" / "versions" / "node",):
            if base.is_dir():
                for version_dir in base.iterdir():
                    paths.append((version_dir / "bin" / self._exe("node"), "nvm", version_dir))
        for base in (
            self.home / ".fnm" / "node-versions",
            self.home / ".local" / "share" / "fnm" / "node-versions",
        ):
            if base.is_dir():
                for version_dir in base.iterdir():
                    paths.append((version_dir / "installation" / "bin" / self._exe("node"), "fnm", version_dir / "installation"))
        for base, source in (
            (self.home / ".mise" / "installs" / "node", "mise"),
            (self.home / ".local" / "share" / "mise" / "installs" / "node", "mise"),
            (self.home / ".asdf" / "installs" / "nodejs", "asdf"),
            (self.home / ".nodenv" / "versions", "nodenv"),
            (self.home / ".n" / "versions" / "node", "n"),
            (Path("/usr/local/n/versions/node"), "n"),
        ):
            if base.is_dir():
                for version_dir in base.iterdir():
                    paths.append((version_dir / "bin" / self._exe("node"), source, version_dir))
        nodebrew = self.home / ".nodebrew"
        paths.append(
            (
                nodebrew / "current" / "bin" / self._exe("node"),
                "nodebrew",
                nodebrew / "current",
            )
        )
        nodebrew_versions = nodebrew / "node"
        if nodebrew_versions.is_dir():
            for version_dir in nodebrew_versions.iterdir():
                paths.append(
                    (
                        version_dir / "bin" / self._exe("node"),
                        "nodebrew",
                        version_dir,
                    )
                )
        volta = self.home / ".volta"
        paths.append((volta / "bin" / self._exe("node"), "volta", volta))
        return self._build_candidates("node", paths, ["node", "npm", "npx", "corepack", "pnpm", "yarn"])

    def _python_candidates(self) -> list[ToolchainCandidate]:
        paths: list[tuple[Path, str, Path]] = []
        for name in ("python3", "python"):
            current = shutil.which(name)
            if current:
                executable = Path(current)
                paths.append((executable, "path", executable.parent.parent))
        for root in self._standard_roots():
            paths.extend(
                [
                    (root / "bin" / self._exe("python3"), "standard", root),
                    (root / "bin" / self._exe("python"), "standard", root),
                ]
            )
        pyenv = self.home / ".pyenv" / "versions"
        if pyenv.is_dir():
            for version_dir in pyenv.iterdir():
                paths.append((version_dir / "bin" / self._exe("python"), "pyenv", version_dir))
        for base, source in (
            (self.home / ".mise" / "installs" / "python", "mise"),
            (self.home / ".local" / "share" / "mise" / "installs" / "python", "mise"),
            (self.home / ".asdf" / "installs" / "python", "asdf"),
        ):
            if base.is_dir():
                for version_dir in base.iterdir():
                    paths.append((version_dir / "bin" / self._exe("python"), source, version_dir))
        return self._build_candidates("python", paths, ["python", "python3", "pip", "pip3"])

    def _go_candidates(self) -> list[ToolchainCandidate]:
        paths: list[tuple[Path, str, Path]] = []
        current = shutil.which("go")
        if current:
            executable = Path(current)
            paths.append((executable, "path", executable.parent.parent))
        for root in self._standard_roots():
            paths.append((root / "bin" / self._exe("go"), "standard", root))
        paths.append((Path("/usr/local/go/bin") / self._exe("go"), "standard", Path("/usr/local/go")))
        goenv = self.home / ".goenv" / "versions"
        if goenv.is_dir():
            for version_dir in goenv.iterdir():
                paths.append((version_dir / "bin" / self._exe("go"), "goenv", version_dir))
        for base, source in (
            (self.home / ".mise" / "installs" / "go", "mise"),
            (self.home / ".local" / "share" / "mise" / "installs" / "go", "mise"),
            (self.home / ".asdf" / "installs" / "golang", "asdf"),
        ):
            if base.is_dir():
                for version_dir in base.iterdir():
                    paths.append((version_dir / "bin" / self._exe("go"), source, version_dir))
        return self._build_candidates("go", paths, ["go", "gofmt"])

    def _build_candidates(
        self,
        kind: str,
        paths: list[tuple[Path, str, Path]],
        executable_names: list[str],
    ) -> list[ToolchainCandidate]:
        seen: set[str] = set()
        result: list[ToolchainCandidate] = []
        for candidate, source, root in paths:
            if not self._executable_file(candidate):
                continue
            try:
                resolved = candidate.resolve()
                bin_dir = candidate.parent.resolve()
                resolved_root = root.expanduser().resolve()
            except OSError:
                continue
            key = os.path.normcase(str(resolved))
            if key in seen or not self._trusted_executable(resolved, bin_dir, resolved_root):
                continue
            seen.add(key)
            version = self._read_version(kind, resolved, bin_dir)
            if not version:
                continue
            executables: dict[str, str] = {}
            for name in executable_names:
                for filename in self._executable_filenames(name):
                    item = bin_dir / filename
                    if self._executable_file(item):
                        executables[name] = str(item.resolve())
                        break
            result.append(
                ToolchainCandidate(
                    kind=kind,
                    version=version,
                    source=source,
                    root=resolved_root,
                    bin_dir=bin_dir,
                    executables=executables,
                )
            )
        return result

    def _read_version(self, kind: str, executable: Path, bin_dir: Path) -> str:
        args = [str(executable), "--version"] if kind != "go" else [str(executable), "version"]
        env = {
            "PATH": os.pathsep.join([str(bin_dir), *self._system_path_entries()]),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        try:
            completed = subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=3,
                shell=False,
                env=env,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if completed.returncode != 0:
            return ""
        return _normalize_version(completed.stdout)

    def _trusted_executable(self, executable: Path, bin_dir: Path, root: Path) -> bool:
        if not _within(executable, root) and not _within(executable, bin_dir):
            # Homebrew/system symlinks resolve outside bin_dir but remain under
            # one of the explicit standard roots.
            if not any(_within(executable, allowed) for allowed in self._standard_roots()):
                return False
        try:
            for path in (executable, bin_dir):
                mode = path.stat().st_mode
                if mode & stat.S_IWOTH:
                    return False
        except OSError:
            return False
        return True

    def _standard_roots(self) -> list[Path]:
        system = platform.system().lower()
        if system == "darwin":
            return [Path("/opt/homebrew"), Path("/usr/local"), Path("/usr")]
        if system == "linux":
            return [Path("/usr/local"), Path("/usr"), Path("/opt")]
        roots: list[Path] = []
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            raw = os.environ.get(variable, "").strip()
            if raw:
                roots.append(Path(raw))
        return roots

    def _system_path_entries(self) -> list[str]:
        if os.name == "nt":
            root = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
            return [str(root / "System32"), str(root)]
        return ["/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]

    @staticmethod
    def _executable_file(path: Path) -> bool:
        try:
            return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))
        except OSError:
            return False

    @staticmethod
    def _exe(name: str) -> str:
        return f"{name}.exe" if os.name == "nt" else name

    @staticmethod
    def _executable_filenames(name: str) -> list[str]:
        if os.name == "nt":
            return [f"{name}.exe", f"{name}.cmd", f"{name}.bat", name]
        return [name]

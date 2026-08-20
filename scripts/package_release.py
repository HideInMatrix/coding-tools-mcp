#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from agent_workbench.version import current_version


ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
APP_NAME = "MicroMatrix Workbench"
INNO_SCRIPT = ROOT / "deploy" / "windows" / "MicroMatrixWorkbench.iss"
HDIUTIL_CREATE_ATTEMPTS = 4
HDIUTIL_RETRY_BASE_SECONDS = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package the PyInstaller desktop build for distribution."
    )
    parser.add_argument(
        "--version",
        required=False,
        help="Deprecated compatibility option. Release filenames no longer contain versions.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "release",
        help="Directory for release archives.",
    )
    return parser.parse_args()


def architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86", "i386", "i686"}:
        return "x86"
    return machine.replace(" ", "-")


def platform_label() -> str:
    system = platform.system().lower()
    arch = architecture()

    if system == "windows":
        return f"windows-{arch}"
    if system == "darwin":
        return f"macos-{arch}"
    if system == "linux":
        return f"linux-{arch}"
    raise SystemExit(f"Unsupported platform: {platform.system()} {platform.machine()}")


def release_base_name() -> str:
    return f"MicroMatrix-Workbench-{platform_label()}"


def write_sha256(path: Path) -> Path:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    checksum = path.with_name(path.name + ".sha256")
    checksum.write_text(
        f"{digest.hexdigest()}  {path.name}\n",
        encoding="utf-8",
    )
    return checksum


def _resolve_inno_compiler() -> Path:
    for command in ("ISCC.exe", "ISCC"):
        executable = shutil.which(command)
        if executable:
            return Path(executable)

    roots = [
        os.environ.get("ProgramFiles(x86)", ""),
        os.environ.get("ProgramFiles", ""),
    ]
    for raw_root in roots:
        if not raw_root:
            continue
        root = Path(raw_root)
        for directory in ("Inno Setup 7", "Inno Setup 6"):
            candidate = root / directory / "ISCC.exe"
            if candidate.is_file():
                return candidate
    raise SystemExit(
        "Windows release packaging requires Inno Setup (ISCC.exe)."
    )


def _inno_architecture(value: str | None = None) -> str:
    arch = value or architecture()
    if arch == "x64":
        return "x64compatible"
    if arch == "arm64":
        return "arm64"
    if arch == "x86":
        return "x86compatible"
    raise SystemExit(f"Unsupported Windows installer architecture: {arch}")


def package_windows(output_base: Path, *, version: str | None = None) -> list[Path]:
    source = DIST_DIR / APP_NAME
    executable = source / f"{APP_NAME}.exe"
    if not source.is_dir() or not executable.is_file():
        raise SystemExit(f"PyInstaller onedir output not found: {source}")
    if not INNO_SCRIPT.is_file():
        raise SystemExit(f"Inno Setup script not found: {INNO_SCRIPT}")

    installer = Path(f"{output_base}.exe")
    installer.unlink(missing_ok=True)
    output_base.parent.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    environment.update(
        {
            "MICROMATRIX_WORKBENCH_INSTALLER_VERSION": version or current_version(),
            "MICROMATRIX_WORKBENCH_INSTALLER_SOURCE": str(source.resolve()),
            "MICROMATRIX_WORKBENCH_INSTALLER_OUTPUT_DIR": str(output_base.parent.resolve()),
            "MICROMATRIX_WORKBENCH_INSTALLER_OUTPUT_BASE": output_base.name,
            "MICROMATRIX_WORKBENCH_INSTALLER_ARCH": _inno_architecture(),
        }
    )
    subprocess.run(
        [str(_resolve_inno_compiler()), str(INNO_SCRIPT)],
        check=True,
        env=environment,
    )
    if not installer.is_file() or installer.stat().st_size <= 0:
        raise RuntimeError(f"Inno Setup did not produce installer: {installer}")
    return [installer]


def package_linux(output_base: Path) -> Path:
    source = DIST_DIR / APP_NAME
    if not source.is_dir():
        raise SystemExit(f"PyInstaller output not found: {source}")

    archive = Path(f"{output_base}.tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=APP_NAME)
    return archive


def _create_macos_dmg(
    staging: Path,
    image: Path,
    *,
    attempts: int = HDIUTIL_CREATE_ATTEMPTS,
) -> None:
    """Create a compressed DMG without reusing hdiutil's transient state.

    Hosted macOS runners occasionally return ``Resource busy`` while Disk
    Images.framework is releasing a previous temporary device. Build every
    attempt at a fresh path/TMPDIR, remove failed partial images and retry with
    bounded backoff. The release artifact is replaced only after a successful
    create, so a partial DMG can never be uploaded.
    """

    image.parent.mkdir(parents=True, exist_ok=True)
    retry_count = max(1, attempts)
    last_error: subprocess.CalledProcessError | None = None
    if hasattr(os, "sync"):
        os.sync()

    with tempfile.TemporaryDirectory(prefix="micromatrix-workbench-hdiutil-") as raw_temp:
        temp_root = Path(raw_temp)
        for attempt in range(1, retry_count + 1):
            attempt_dir = temp_root / f"attempt-{attempt}"
            attempt_dir.mkdir()
            candidate = attempt_dir / image.name
            environment = os.environ.copy()
            environment["TMPDIR"] = str(attempt_dir)
            try:
                subprocess.run(
                    [
                        "/usr/bin/hdiutil",
                        "create",
                        "-volname",
                        APP_NAME,
                        "-srcfolder",
                        str(staging),
                        "-ov",
                        "-format",
                        "UDZO",
                        str(candidate),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
            except subprocess.CalledProcessError as exc:
                last_error = exc
                candidate.unlink(missing_ok=True)
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                if attempt >= retry_count:
                    break
                delay = HDIUTIL_RETRY_BASE_SECONDS * attempt
                print(
                    f"hdiutil create attempt {attempt}/{retry_count} failed: "
                    f"{detail or 'unknown error'}; retrying in {delay:g}s",
                    flush=True,
                )
                time.sleep(delay)
                continue

            if not candidate.is_file() or candidate.stat().st_size <= 0:
                raise RuntimeError("hdiutil reported success but produced no DMG")
            image.unlink(missing_ok=True)
            shutil.move(str(candidate), str(image))
            return

    detail = ""
    if last_error is not None:
        detail = (last_error.stderr or last_error.stdout or str(last_error)).strip()
    raise RuntimeError(
        f"hdiutil could not create {image.name} after {retry_count} attempts: "
        f"{detail or 'unknown error'}"
    ) from last_error


def package_macos(output_base: Path) -> list[Path]:
    source = DIST_DIR / f"{APP_NAME}.app"
    if not source.is_dir():
        raise SystemExit(f"PyInstaller app bundle not found: {source}")

    image = Path(f"{output_base}.dmg")
    with tempfile.TemporaryDirectory(prefix="micromatrix-workbench-dmg-") as temp_dir:
        staging = Path(temp_dir) / APP_NAME
        staging.mkdir()
        shutil.copytree(source, staging / source.name, symlinks=True)
        os.symlink("/Applications", staging / "Applications")

        _create_macos_dmg(staging, image)

    # The DMG remains the human-friendly first-install artifact.  The ZIP is
    # consumed by the in-app updater because macOS can extract it without
    # mounting a disk image and it preserves the .app bundle structure.
    updater_archive = Path(f"{output_base}.zip")
    updater_archive.unlink(missing_ok=True)
    subprocess.run(
        [
            "/usr/bin/ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(source),
            str(updater_archive),
        ],
        check=True,
    )

    return [image, updater_archive]


def main() -> int:
    args = parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = release_base_name()
    output_base = output_dir / base_name

    system = platform.system().lower()
    if system == "windows":
        packages = package_windows(output_base)
    elif system == "darwin":
        packages = package_macos(output_base)
    elif system == "linux":
        packages = [package_linux(output_base)]
    else:
        raise SystemExit(f"Unsupported platform: {platform.system()}")

    for package in packages:
        checksum = write_sha256(package)
        print(f"Package : {package}")
        print(f"SHA256  : {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
APP_NAME = "Coding Tools MCP"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package the PyInstaller desktop build for distribution."
    )
    parser.add_argument(
        "--version",
        required=True,
        help="Version string used in release filenames, e.g. 1.2.0 or v1.2.0.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "release",
        help="Directory for release archives.",
    )
    return parser.parse_args()


def normalized_version(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("v") and len(value) > 1:
        value = value[1:]
    return value.replace("/", "-").replace("\\", "-")


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
        return "macos-intel" if arch == "x64" else "macos-apple-silicon"
    if system == "linux":
        return f"linux-{arch}"
    raise SystemExit(f"Unsupported platform: {platform.system()} {platform.machine()}")


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


def package_windows(output_base: Path) -> Path:
    source = DIST_DIR / APP_NAME
    if not source.is_dir():
        raise SystemExit(f"PyInstaller output not found: {source}")

    archive = shutil.make_archive(
        str(output_base),
        "zip",
        root_dir=DIST_DIR,
        base_dir=APP_NAME,
    )
    return Path(archive)


def package_linux(output_base: Path) -> Path:
    source = DIST_DIR / APP_NAME
    if not source.is_dir():
        raise SystemExit(f"PyInstaller output not found: {source}")

    archive = Path(f"{output_base}.tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=APP_NAME)
    return archive


def package_macos(output_base: Path) -> Path:
    source = DIST_DIR / f"{APP_NAME}.app"
    if not source.is_dir():
        raise SystemExit(f"PyInstaller app bundle not found: {source}")

    image = Path(f"{output_base}.dmg")
    with tempfile.TemporaryDirectory(prefix="coding-tools-mcp-dmg-") as temp_dir:
        staging = Path(temp_dir) / APP_NAME
        staging.mkdir()
        shutil.copytree(source, staging / source.name, symlinks=True)
        os.symlink("/Applications", staging / "Applications")

        subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                APP_NAME,
                "-srcfolder",
                str(staging),
                "-ov",
                "-format",
                "UDZO",
                str(image),
            ],
            check=True,
        )

    return image


def main() -> int:
    args = parse_args()
    version = normalized_version(args.version)
    if not version:
        raise SystemExit("Version must not be empty.")

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"Coding-Tools-MCP-{version}-{platform_label()}"
    output_base = output_dir / base_name

    system = platform.system().lower()
    if system == "windows":
        package = package_windows(output_base)
    elif system == "darwin":
        package = package_macos(output_base)
    elif system == "linux":
        package = package_linux(output_base)
    else:
        raise SystemExit(f"Unsupported platform: {platform.system()}")

    checksum = write_sha256(package)
    print(f"Package : {package}")
    print(f"SHA256  : {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

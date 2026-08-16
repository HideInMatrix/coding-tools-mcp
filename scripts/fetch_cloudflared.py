#!/usr/bin/env python3

from __future__ import annotations

import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "cloudflared"
LATEST_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"


def target() -> tuple[str, str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"

    if system == "darwin":
        return (
            f"cloudflared-darwin-{arch}.tgz",
            f"darwin-{arch}",
            "cloudflared",
        )
    if system == "windows":
        return (
            f"cloudflared-windows-{arch}.exe",
            f"windows-{arch}",
            "cloudflared.exe",
        )
    if system == "linux":
        return (
            f"cloudflared-linux-{arch}",
            f"linux-{arch}",
            "cloudflared",
        )
    raise SystemExit(f"Unsupported platform: {platform.system()} {machine}")


def main() -> int:
    asset, platform_dir, executable_name = target()
    url = f"{LATEST_BASE}/{asset}"
    destination_dir = VENDOR / platform_dir
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / executable_name

    print(f"Downloading {url}")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir) / asset
        urllib.request.urlretrieve(url, temp)

        if asset.endswith(".tgz"):
            with tarfile.open(temp, "r:gz") as archive:
                member = next(
                    item for item in archive.getmembers()
                    if Path(item.name).name == "cloudflared"
                )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise RuntimeError("cloudflared archive is invalid")
                with destination.open("wb") as output:
                    shutil.copyfileobj(extracted, output)
        else:
            shutil.copy2(temp, destination)

    if os.name != "nt":
        destination.chmod(0o755)

    print(f"Saved to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

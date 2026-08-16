#!/usr/bin/env python3

from __future__ import annotations

import os
import platform
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "cloudflared"
LATEST_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"


def target_candidates(
    system: str | None = None,
    machine: str | None = None,
) -> tuple[list[str], str, str]:
    """Return download candidates and the local bundle destination.

    Cloudflare currently does not publish a Windows ARM64 release asset.  Keep
    the desktop package itself ARM64-native, but allow the bundled cloudflared
    helper to fall back to the official Windows AMD64 binary, which Windows 11
    on ARM can execute through its x64 compatibility layer.

    The native ARM64 filename is still tried first so this script will
    automatically start using it if Cloudflare adds that asset in a future
    release.
    """

    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"

    if system == "darwin":
        return (
            [f"cloudflared-darwin-{arch}.tgz"],
            f"darwin-{arch}",
            "cloudflared",
        )
    if system == "windows":
        assets = [f"cloudflared-windows-{arch}.exe"]
        if arch == "arm64":
            assets.append("cloudflared-windows-amd64.exe")
        return (
            assets,
            f"windows-{arch}",
            "cloudflared.exe",
        )
    if system == "linux":
        return (
            [f"cloudflared-linux-{arch}"],
            f"linux-{arch}",
            "cloudflared",
        )
    raise SystemExit(f"Unsupported platform: {platform.system()} {machine}")


def download_first_available(assets: list[str], temp_dir: Path) -> tuple[str, Path]:
    """Download the first published release asset from ``assets``.

    Only a 404 is treated as an unavailable architecture and triggers the next
    candidate. Authentication failures, rate limiting and transient GitHub
    failures are surfaced immediately instead of being hidden as a fallback.
    """

    for index, asset in enumerate(assets):
        url = f"{LATEST_BASE}/{asset}"
        temp = temp_dir / asset
        print(f"Downloading {url}")
        try:
            urllib.request.urlretrieve(url, temp)
            return asset, temp
        except urllib.error.HTTPError as exc:
            has_fallback = index + 1 < len(assets)
            if exc.code != 404 or not has_fallback:
                raise
            next_asset = assets[index + 1]
            print(
                f"Release asset {asset} is not published; "
                f"falling back to {next_asset}."
            )

    raise RuntimeError("No cloudflared release asset candidates were configured")


def main() -> int:
    assets, platform_dir, executable_name = target_candidates()
    destination_dir = VENDOR / platform_dir
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / executable_name

    with tempfile.TemporaryDirectory() as temp_dir:
        asset, temp = download_first_available(assets, Path(temp_dir))

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

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib.metadata
import platform
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify that desktop dependencies match the runner architecture."
    )
    parser.add_argument(
        "--expected-arch",
        choices=("x64", "arm64"),
        required=True,
    )
    return parser.parse_args()


def normalized_architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise SystemExit(f"Required package is not installed: {name}") from exc


def main() -> int:
    args = parse_args()
    actual_arch = normalized_architecture()

    print(f"Python       : {sys.version.split()[0]}")
    print(f"Platform     : {platform.platform()}")
    print(f"Machine      : {platform.machine()}")
    print(f"Architecture : {actual_arch}")
    print(f"PySide6      : {package_version('PySide6')}")
    print(f"PyInstaller  : {package_version('pyinstaller')}")
    print(f"MCP Server   : {package_version('coding-tools-mcp')}")

    if actual_arch != args.expected_arch:
        raise SystemExit(
            "Runner architecture mismatch: "
            f"expected {args.expected_arch}, got {actual_arch}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

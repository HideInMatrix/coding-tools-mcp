#!/usr/bin/env python3

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import coding_tools_mcp

mcp_server_version = coding_tools_mcp.__version__
mcp_server_file = Path(coding_tools_mcp.__file__).resolve()


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
    import importlib.metadata

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
    print(f"pywebview    : {package_version('pywebview')}")
    print(f"PyInstaller  : {package_version('pyinstaller')}")
    print(f"MCP Server   : {mcp_server_version} (in-tree)")
    print(f"MCP Source   : {mcp_server_file}")

    try:
        mcp_server_file.relative_to(ROOT)
    except ValueError as exc:
        raise SystemExit(
            "MCP Server import resolved outside this repository: "
            f"{mcp_server_file}"
        ) from exc

    if actual_arch != args.expected_arch:
        raise SystemExit(
            "Runner architecture mismatch: "
            f"expected {args.expected_arch}, got {actual_arch}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import sys

from .mcp_process import run_internal_mcp_server


def main() -> int:
    return run_internal_mcp_server(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())


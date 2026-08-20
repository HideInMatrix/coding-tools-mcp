from __future__ import annotations

import sys

from .mcp_process import INTERNAL_MCP_FLAG, run_internal_mcp_server
from .resources import web_ui_entrypoint
from .version import current_version
from agent_runtime.sandbox.windows_launcher import (
    INTERNAL_WINDOWS_SANDBOX_FLAG,
    run_internal_windows_sandbox_process,
)


def main() -> int:
    if INTERNAL_WINDOWS_SANDBOX_FLAG in sys.argv:
        index = sys.argv.index(INTERNAL_WINDOWS_SANDBOX_FLAG)
        return run_internal_windows_sandbox_process(sys.argv[index + 1 :])

    if INTERNAL_MCP_FLAG in sys.argv:
        index = sys.argv.index(INTERNAL_MCP_FLAG)
        return run_internal_mcp_server(sys.argv[index + 1 :])

    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "桌面版需要 pywebview。开发环境请执行 pip install -r requirements-desktop.txt"
        ) from exc

    from .desktop_api import DesktopAPI

    entrypoint = web_ui_entrypoint()
    if not entrypoint.is_file():
        raise RuntimeError(
            "找不到桌面 Web UI 构建产物。请先在 agent_workbench/web 下执行 npm install && npm run build。"
        )

    app_version = current_version()
    api = DesktopAPI(app_version=app_version)
    window = webview.create_window(
        f"MicroMatrix Workbench {app_version}",
        str(entrypoint),
        js_api=api,
        width=1180,
        height=780,
        min_size=(960, 680),
        background_color="#fdfdfd",
    )
    api._bind_window(window)
    window.events.closing += api._close
    webview.start(http_server=True, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

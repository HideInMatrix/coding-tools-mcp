from __future__ import annotations

import sys

from .mcp_process import INTERNAL_MCP_FLAG, run_internal_mcp_server
from .version import current_version


def main() -> int:
    if INTERNAL_MCP_FLAG in sys.argv:
        index = sys.argv.index(INTERNAL_MCP_FLAG)
        return run_internal_mcp_server(sys.argv[index + 1 :])

    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        raise RuntimeError(
            "桌面版需要 PySide6。开发环境请执行 pip install -r requirements-desktop.txt"
        ) from exc

    from .ui.main_window import MainWindow

    QCoreApplication.setOrganizationName("MicroMatrix")
    QCoreApplication.setOrganizationDomain("micromatrix.org")
    QCoreApplication.setApplicationName("Coding Tools MCP")
    QCoreApplication.setApplicationVersion(current_version())

    app = QApplication(sys.argv)
    app.setApplicationDisplayName("Coding Tools MCP")
    app.setQuitOnLastWindowClosed(True)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

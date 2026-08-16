from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..executables import ExecutableCandidate


class ExecutableSelector(QWidget):
    detection_requested = Signal()
    selection_requested = Signal()

    def __init__(self, display_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.display_name = display_name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("留空时优先使用应用内置客户端，再自动检测系统安装")
        self.detect_button = QPushButton("自动检测")
        self.choose_button = QPushButton("选择…")
        self.detect_button.clicked.connect(self.detection_requested.emit)
        self.choose_button.clicked.connect(self.selection_requested.emit)
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.detect_button)
        row.addWidget(self.choose_button)
        layout.addLayout(row)

        self.status_label = QLabel("未检测")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: palette(mid); font-size: 12px;")
        layout.addWidget(self.status_label)

    def configured_path(self) -> str:
        return self.path_edit.text().strip()

    def set_configured_path(self, value: str) -> None:
        self.path_edit.setText(value)
        if value:
            self.status_label.setText("已保存客户端路径，启动时会再次验证。")
        else:
            self.status_label.setText("未检测")

    def set_detecting(self) -> None:
        self.detect_button.setEnabled(False)
        self.choose_button.setEnabled(False)
        self.status_label.setText("正在检测客户端…")

    def set_candidate(self, candidate: ExecutableCandidate) -> None:
        self.detect_button.setEnabled(True)
        self.choose_button.setEnabled(True)
        if candidate.source == "manual":
            self.path_edit.setText(str(candidate.path))
        summary = (
            f"✓ {self.display_name} {candidate.version} · {candidate.source_label} · {candidate.path}"
        )
        if candidate.warning:
            summary += f" · {candidate.warning}"
            self.status_label.setStyleSheet("color: #E6A23C; font-size: 12px;")
        else:
            self.status_label.setStyleSheet("color: #67C23A; font-size: 12px;")
        self.status_label.setText(summary)

    def set_error(self, message: str) -> None:
        self.detect_button.setEnabled(True)
        self.choose_button.setEnabled(True)
        self.status_label.setStyleSheet("color: #F56C6C; font-size: 12px;")
        self.status_label.setText(f"未检测到可用客户端：{message}")

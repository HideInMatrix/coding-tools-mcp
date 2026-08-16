from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from ..config import LaunchConfig
from ..launcher import MCPLauncher
from ..user_settings import load_settings, save_settings


class Bridge(QObject):
    log = Signal(str)
    started = Signal(object)
    failed = Signal(str)
    stopped = Signal()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Coding Tools MCP")
        # macOS 下 700px 左右的初始宽度会让 QFormLayout 的标签列、
        # 输入框和右侧按钮互相挤压。默认给表单更充足的横向空间，
        # 同时保留一个不会把输入框压扁的最小尺寸。
        self.resize(860, 760)
        self.setMinimumSize(780, 680)

        self.bridge = Bridge()
        self.bridge.log.connect(self._append_log)
        self.bridge.started.connect(self._on_started)
        self.bridge.failed.connect(self._on_failed)
        self.bridge.stopped.connect(self._on_stopped)

        self.launcher = MCPLauncher(log=self.bridge.log.emit)
        self._busy = False
        self._build_ui()
        self._restore_settings()

        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._poll_health)
        self._health_timer.start(700)

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(18)

        title = QLabel("Coding Tools MCP")
        title_font = QFont()
        title_font.setPointSize(24)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)

        subtitle = QLabel("把本地代码目录安全地连接到支持 MCP 的客户端")
        subtitle.setStyleSheet("color: palette(mid);")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        form_card = QFrame()
        form_card.setFrameShape(QFrame.Shape.StyledPanel)
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(20, 18, 20, 18)
        form_layout.setSpacing(14)

        section = QLabel("连接设置")
        section_font = QFont()
        section_font.setWeight(QFont.Weight.DemiBold)
        section.setFont(section_font)
        form_layout.addWidget(section)

        workspace_row = QHBoxLayout()
        workspace_row.setSpacing(8)
        self.workspace_edit = QLineEdit()
        self.workspace_edit.setFixedWidth(426)
        self.workspace_edit.setPlaceholderText("选择需要授权给 MCP 的代码目录")
        choose_button = QPushButton("选择…")
        choose_button.setFixedWidth(86)
        choose_button.clicked.connect(self._choose_workspace)
        workspace_row.addWidget(self.workspace_edit)
        workspace_row.addWidget(choose_button)
        workspace_row.addStretch(1)

        form = QFormLayout()
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint
        )
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        form.addRow("Workspace", workspace_row)

        self.client_id_edit = QLineEdit()
        self.client_id_edit.setFixedWidth(520)
        self.client_id_edit.setPlaceholderText("Cloudflare OAuth Client ID")
        form.addRow("Client ID", self.client_id_edit)

        self.client_secret_edit = QLineEdit()
        self.client_secret_edit.setFixedWidth(520)
        self.client_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_secret_edit.setPlaceholderText("OAuth Client Secret")
        form.addRow("Client Secret", self.client_secret_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setFixedWidth(520)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("MCP OAuth 登录密码")
        form.addRow("Password", self.password_edit)

        self.server_url_edit = QLineEdit()
        self.server_url_edit.setFixedWidth(520)
        self.server_url_edit.setPlaceholderText(
            "可选，例如 https://mcp.example.com；留空使用 Quick Tunnel"
        )
        form.addRow("Public URL", self.server_url_edit)
        form_layout.addLayout(form)

        self.remember_secrets = QCheckBox("在这台电脑上保存 Client Secret 和 Password")
        self.remember_secrets.setChecked(True)
        form_layout.addWidget(self.remember_secrets)
        secret_note = QLabel(
            "当前版本保存在用户配置目录并限制文件权限；后续可接入 macOS Keychain。"
        )
        secret_note.setWordWrap(True)
        secret_note.setStyleSheet("color: palette(mid); font-size: 12px;")
        form_layout.addWidget(secret_note)
        outer.addWidget(form_card)

        status_card = QFrame()
        status_card.setFrameShape(QFrame.Shape.StyledPanel)
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(20, 16, 20, 16)
        status_layout.setSpacing(10)

        status_top = QHBoxLayout()
        self.status_label = QLabel("●  Stopped")
        self.status_label.setStyleSheet("color: palette(mid);")
        self.mode_label = QLabel("Quick Tunnel")
        self.mode_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.mode_label.setStyleSheet("color: palette(mid);")
        status_top.addWidget(self.status_label)
        status_top.addStretch(1)
        status_top.addWidget(self.mode_label)
        status_layout.addLayout(status_top)

        url_row = QHBoxLayout()
        self.public_url_edit = QLineEdit()
        self.public_url_edit.setMinimumWidth(460)
        self.public_url_edit.setReadOnly(True)
        self.public_url_edit.setPlaceholderText("启动后会显示 Public MCP URL")
        copy_button = QPushButton("复制")
        copy_button.clicked.connect(self._copy_public_url)
        url_row.addWidget(self.public_url_edit, 1)
        url_row.addWidget(copy_button)
        status_layout.addLayout(url_row)
        outer.addWidget(status_card)

        controls = QHBoxLayout()
        controls.addItem(
            QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )
        self.start_button = QPushButton("启动 MCP")
        self.start_button.setDefault(True)
        self.start_button.setMinimumWidth(130)
        self.start_button.clicked.connect(self._toggle_service)
        controls.addWidget(self.start_button)
        outer.addLayout(controls)

        logs_header = QHBoxLayout()
        logs_title = QLabel("运行日志")
        logs_title.setFont(section_font)
        clear_button = QPushButton("清空")
        clear_button.setFlat(True)
        clear_button.clicked.connect(lambda: self.logs.clear())
        logs_header.addWidget(logs_title)
        logs_header.addStretch(1)
        logs_header.addWidget(clear_button)
        outer.addLayout(logs_header)

        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1000)
        self.logs.setMinimumHeight(120)
        outer.addWidget(self.logs, 1)

    def _choose_workspace(self) -> None:
        current = self.workspace_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择 Workspace", current)
        if path:
            self.workspace_edit.setText(path)

    def _config_from_form(self) -> LaunchConfig:
        return LaunchConfig(
            workspace=Path(self.workspace_edit.text().strip()),
            oauth_client_id=self.client_id_edit.text(),
            oauth_client_secret=self.client_secret_edit.text(),
            oauth_password=self.password_edit.text(),
            server_url=self.server_url_edit.text(),
        ).validated()

    def _toggle_service(self) -> None:
        if self._busy:
            return
        if self.launcher.is_running:
            self._stop_async()
        else:
            self._start_async()

    def _start_async(self) -> None:
        try:
            config = self._config_from_form()
        except Exception as exc:
            QMessageBox.warning(self, "配置有误", str(exc))
            return

        self._save_settings()
        self._busy = True
        self.start_button.setEnabled(False)
        self.start_button.setText("正在启动…")
        self.status_label.setText("●  Starting")

        def worker() -> None:
            try:
                info = self.launcher.start(config)
            except Exception as exc:
                self.bridge.failed.emit(str(exc))
            else:
                self.bridge.started.emit(info)

        threading.Thread(target=worker, daemon=True).start()

    def _stop_async(self) -> None:
        self._busy = True
        self.start_button.setEnabled(False)
        self.start_button.setText("正在停止…")

        def worker() -> None:
            self.launcher.stop()
            self.bridge.stopped.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _on_started(self, info: object) -> None:
        public_url = getattr(info, "public_mcp_url")
        mode = getattr(info, "url_mode")
        self.public_url_edit.setText(public_url)
        self.mode_label.setText(mode)
        self.status_label.setText("●  Running")
        self.start_button.setText("停止 MCP")
        self.start_button.setEnabled(True)
        self._busy = False

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.start_button.setEnabled(True)
        self.start_button.setText("启动 MCP")
        self.status_label.setText("●  Error")
        QMessageBox.critical(self, "启动失败", message)

    def _on_stopped(self) -> None:
        self._busy = False
        self.start_button.setEnabled(True)
        self.start_button.setText("启动 MCP")
        self.status_label.setText("●  Stopped")
        self.public_url_edit.clear()

    def _poll_health(self) -> None:
        if self._busy:
            return
        if self.status_label.text().endswith("Running") and not self.launcher.is_running:
            reason = self.launcher.exit_reason
            self._on_stopped()
            if reason:
                QMessageBox.warning(self, "服务已停止", reason)

    def _append_log(self, text: str) -> None:
        self.logs.appendPlainText(text)
        bar = self.logs.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _copy_public_url(self) -> None:
        value = self.public_url_edit.text().strip()
        if value:
            QApplication.clipboard().setText(value)

    def _restore_settings(self) -> None:
        data = load_settings()
        self.workspace_edit.setText(str(data.get("workspace", "")))
        self.client_id_edit.setText(str(data.get("client_id", "")))
        self.server_url_edit.setText(str(data.get("server_url", "")))
        remember = bool(data.get("remember_secrets", True))
        self.remember_secrets.setChecked(remember)
        if remember:
            self.client_secret_edit.setText(str(data.get("client_secret", "")))
            self.password_edit.setText(str(data.get("password", "")))

    def _save_settings(self) -> None:
        remember = self.remember_secrets.isChecked()
        data: dict[str, object] = {
            "workspace": self.workspace_edit.text().strip(),
            "client_id": self.client_id_edit.text().strip(),
            "server_url": self.server_url_edit.text().strip(),
            "remember_secrets": remember,
        }
        if remember:
            data["client_secret"] = self.client_secret_edit.text()
            data["password"] = self.password_edit.text()
        save_settings(data)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_settings()
        if self.launcher.is_running:
            self.launcher.stop()
        event.accept()

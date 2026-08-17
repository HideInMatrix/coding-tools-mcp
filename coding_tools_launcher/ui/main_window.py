from __future__ import annotations

import threading
from pathlib import Path

from coding_tools_mcp import __version__ as MCP_VERSION
from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
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
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import LaunchConfig, NetworkConfig
from ..executables import resolve_executable
from ..launcher import MCPLauncher
from ..updates import ReleaseInfo, fetch_latest_release
from ..user_settings import load_settings, save_settings
from .executable_selector import ExecutableSelector


class Bridge(QObject):
    log = Signal(str)
    started = Signal(object)
    failed = Signal(str)
    stopped = Signal()
    executable_detected = Signal(str, object)
    executable_detection_failed = Signal(str, str)
    update_checked = Signal(object)
    update_check_failed = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Coding Tools MCP")
        # macOS 下 700px 左右的初始宽度会让 QFormLayout 的标签列、
        # 输入框和右侧按钮互相挤压。默认给表单更充足的横向空间，
        # 同时保留一个不会把输入框压扁的最小尺寸。
        self.resize(980, 760)
        self.setMinimumSize(900, 680)

        self.bridge = Bridge()
        self.bridge.log.connect(self._append_log)
        self.bridge.started.connect(self._on_started)
        self.bridge.failed.connect(self._on_failed)
        self.bridge.stopped.connect(self._on_stopped)
        self.bridge.executable_detected.connect(self._on_executable_detected)
        self.bridge.executable_detection_failed.connect(
            self._on_executable_detection_failed
        )
        self.bridge.update_checked.connect(self._on_update_checked)
        self.bridge.update_check_failed.connect(self._on_update_check_failed)

        self.launcher = MCPLauncher(log=self.bridge.log.emit)
        self._busy = False
        self._checking_update = False
        self._release_info: ReleaseInfo | None = None
        self._executable_selectors: dict[str, ExecutableSelector] = {}
        self._build_ui()
        self._restore_settings()

        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._poll_health)
        self._health_timer.start(700)

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setFrameShape(QFrame.Shape.StyledPanel)
        sidebar.setFixedWidth(92)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 18, 10, 18)
        sidebar_layout.setSpacing(10)

        brand = QLabel("CT")
        brand_font = QFont()
        brand_font.setPointSize(18)
        brand_font.setWeight(QFont.Weight.DemiBold)
        brand.setFont(brand_font)
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setFixedHeight(46)
        sidebar_layout.addWidget(brand)

        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)

        def navigation_button(
            text: str,
            icon: QStyle.StandardPixmap,
        ) -> QToolButton:
            button = QToolButton()
            button.setText(text)
            button.setIcon(self.style().standardIcon(icon))
            button.setIconSize(QSize(24, 24))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setFixedSize(72, 64)
            button.setStyleSheet(
                "QToolButton { border: none; border-radius: 8px; padding: 5px; }"
                "QToolButton:checked { background: palette(alternate-base); }"
            )
            self.navigation_group.addButton(button)
            sidebar_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            return button

        self.home_nav_button = navigation_button(
            "首页",
            QStyle.StandardPixmap.SP_DirHomeIcon,
        )
        self.about_nav_button = navigation_button(
            "关于",
            QStyle.StandardPixmap.SP_MessageBoxInformation,
        )
        sidebar_layout.addStretch(1)

        self.page_stack = QStackedWidget()
        shell.addWidget(sidebar)
        shell.addWidget(self.page_stack, 1)

        home_page = QWidget()
        self.home_page = home_page
        outer = QVBoxLayout(home_page)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(18)
        self.page_stack.addWidget(home_page)

        title = QLabel("Coding Tools MCP")
        title_font = QFont()
        title_font.setPointSize(24)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)

        subtitle = QLabel("把本地代码目录安全地连接到支持 MCP 的客户端")
        subtitle.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        outer.addWidget(title)
        outer.addWidget(subtitle)

        form_card = QFrame()
        form_card.setFrameShape(QFrame.Shape.StyledPanel)
        form_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(20, 18, 20, 18)
        form_layout.setSpacing(14)

        section = QLabel("连接设置")
        section_font = QFont()
        section_font.setWeight(QFont.Weight.DemiBold)
        section.setFont(section_font)
        form_layout.addWidget(section)

        def add_config_row(
            label_text: str,
            field: QWidget,
            trailing: QWidget | None = None,
            *,
            target_layout: QVBoxLayout | None = None,
        ) -> None:
            """Use the same layout model as the Public MCP URL row below.

            The label keeps a stable column width, while the input field has a
            minimum width and receives the stretch factor. This prevents the
            field from being squeezed smaller than its minimum size when the
            window is resized.
            """

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            label = QLabel(label_text)
            label.setFixedWidth(96)
            label.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )
            label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )

            field.setMinimumWidth(460)
            field.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )

            if trailing is not None:
                trailing.setSizePolicy(
                    QSizePolicy.Policy.Fixed,
                    QSizePolicy.Policy.Fixed,
                )

            row.addWidget(label)
            row.addWidget(field, 1)
            if trailing is not None:
                row.addWidget(trailing)

            (target_layout or form_layout).addLayout(row)

        def add_secret_row(
            label_text: str,
            field: QLineEdit,
            *,
            target_layout: QVBoxLayout | None = None,
        ) -> None:
            """Add a masked input with a click-to-show eye button.

            The button changes only the visual echo mode. The field's actual
            text is never modified, so settings persistence and launcher
            behavior remain unchanged.
            """

            field.setEchoMode(QLineEdit.EchoMode.Password)

            visibility_button = QToolButton()
            visibility_button.setText("👁")
            visibility_button.setCheckable(True)
            visibility_button.setAutoRaise(True)
            visibility_button.setFixedSize(30, 30)
            visibility_button.setToolTip("显示内容")
            visibility_button.setAccessibleName(f"显示 {label_text}")

            def toggle_visibility(visible: bool) -> None:
                field.setEchoMode(
                    QLineEdit.EchoMode.Normal
                    if visible
                    else QLineEdit.EchoMode.Password
                )
                visibility_button.setToolTip("隐藏内容" if visible else "显示内容")
                visibility_button.setAccessibleName(
                    f"隐藏 {label_text}" if visible else f"显示 {label_text}"
                )

            visibility_button.toggled.connect(toggle_visibility)
            add_config_row(
                label_text,
                field,
                visibility_button,
                target_layout=target_layout,
            )

        self.workspace_edit = QLineEdit()
        self.workspace_edit.setPlaceholderText("选择需要授权给 MCP 的代码目录")
        choose_button = QPushButton("选择…")
        choose_button.setMinimumWidth(86)
        choose_button.clicked.connect(self._choose_workspace)
        add_config_row("Workspace", self.workspace_edit, choose_button)

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("MCP OAuth 授权页登录密码")
        add_secret_row("Password", self.password_edit)

        self.advanced_oauth_toggle = QCheckBox("高级 OAuth 设置（预注册 Client）")
        self.advanced_oauth_toggle.setToolTip(
            "默认关闭并使用 Dynamic Client Registration；只有不支持 DCR 的客户端才需要预注册 Client。"
        )
        form_layout.addWidget(self.advanced_oauth_toggle)

        self.advanced_oauth_panel = QWidget()
        advanced_oauth_layout = QVBoxLayout(self.advanced_oauth_panel)
        advanced_oauth_layout.setContentsMargins(0, 0, 0, 0)
        advanced_oauth_layout.setSpacing(10)

        self.client_id_edit = QLineEdit()
        self.client_id_edit.setPlaceholderText("自定义 MCP OAuth Client ID；不是 Cloudflare Connector ID")
        add_config_row("Client ID", self.client_id_edit, target_layout=advanced_oauth_layout)

        self.client_secret_edit = QLineEdit()
        self.client_secret_edit.setPlaceholderText("可选；需要 confidential OAuth client 时填写")
        add_secret_row(
            "Client Secret",
            self.client_secret_edit,
            target_layout=advanced_oauth_layout,
        )
        oauth_note = QLabel(
            "ChatGPT 默认通过 /oauth/register 动态生成 Client ID。这里的 Client ID/Secret 仅用于手动预注册 OAuth Client。"
        )
        oauth_note.setWordWrap(True)
        oauth_note.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        oauth_note.setStyleSheet("font-size: 12px;")
        advanced_oauth_layout.addWidget(oauth_note)
        self.advanced_oauth_panel.setVisible(False)
        form_layout.addWidget(self.advanced_oauth_panel)
        self.advanced_oauth_toggle.toggled.connect(self._on_advanced_oauth_toggled)

        self.network_provider_combo = QComboBox()
        for label, key in (
            ("Cloudflare Tunnel", "cloudflare"),
            ("FRP", "frp"),
            ("ngrok", "ngrok"),
            ("Tailscale Funnel", "tailscale"),
            ("自定义公网 URL", "external"),
        ):
            self.network_provider_combo.addItem(label, key)
        add_config_row("网络方案", self.network_provider_combo)

        self.network_stack = QStackedWidget()
        self.network_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        form_layout.addWidget(self.network_stack)
        self._provider_page_indexes: dict[str, int] = {}

        def provider_page(provider: str) -> tuple[QWidget, QVBoxLayout]:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 2, 0, 0)
            layout.setSpacing(10)
            self._provider_page_indexes[provider] = self.network_stack.addWidget(page)
            return page, layout

        def add_note(layout: QVBoxLayout, text: str) -> None:
            note = QLabel(text)
            note.setWordWrap(True)
            note.setForegroundRole(QPalette.ColorRole.PlaceholderText)
            note.setStyleSheet("font-size: 12px;")
            layout.addWidget(note)

        _, cloudflare_layout = provider_page("cloudflare")
        cloudflare_layout.setSpacing(6)
        self.cf_public_url_edit = QLineEdit()
        self.cf_public_url_edit.setPlaceholderText(
            "留空使用 Quick Tunnel；固定域名例如 https://mcp.example.com"
        )
        add_config_row(
            "Public URL",
            self.cf_public_url_edit,
            target_layout=cloudflare_layout,
        )
        self.cf_tunnel_token_edit = QLineEdit()
        self.cf_tunnel_token_edit.setPlaceholderText(
            "固定域名时填写 Cloudflare Named Tunnel --token"
        )
        add_secret_row(
            "Tunnel Token",
            self.cf_tunnel_token_edit,
            target_layout=cloudflare_layout,
        )
        add_note(cloudflare_layout, "Public URL 与 Tunnel Token 都留空时自动创建 Quick Tunnel。")

        _, frp_layout = provider_page("frp")
        self.frp_public_url_edit = QLineEdit()
        self.frp_public_url_edit.setPlaceholderText("例如 https://mcp.example.com")
        add_config_row("Public URL", self.frp_public_url_edit, target_layout=frp_layout)
        self.frp_executable_selector = ExecutableSelector("frpc")
        self.frp_executable_edit = self.frp_executable_selector.path_edit
        add_config_row(
            "frpc",
            self.frp_executable_selector,
            target_layout=frp_layout,
        )
        self._register_executable_selector("frpc", self.frp_executable_selector)
        self.frp_config_edit = QLineEdit()
        self.frp_config_edit.setPlaceholderText("frpc.toml / frpc.yaml 配置文件")
        frp_choose_button = QPushButton("选择…")
        frp_choose_button.setMinimumWidth(86)
        frp_choose_button.clicked.connect(self._choose_frp_config)
        add_config_row(
            "FRP Config",
            self.frp_config_edit,
            frp_choose_button,
            target_layout=frp_layout,
        )
        add_note(
            frp_layout,
            "FRP 服务端和 HTTPS 域名由你自行管理；frpc 配置需把公网入口转发到本机 MCP 端口。",
        )

        _, ngrok_layout = provider_page("ngrok")
        self.ngrok_public_url_edit = QLineEdit()
        self.ngrok_public_url_edit.setPlaceholderText("可选：保留域名；留空使用 ngrok 动态 HTTPS URL")
        add_config_row("Public URL", self.ngrok_public_url_edit, target_layout=ngrok_layout)
        self.ngrok_executable_selector = ExecutableSelector("ngrok")
        self.ngrok_executable_edit = self.ngrok_executable_selector.path_edit
        add_config_row(
            "ngrok",
            self.ngrok_executable_selector,
            target_layout=ngrok_layout,
        )
        self._register_executable_selector("ngrok", self.ngrok_executable_selector)
        self.ngrok_authtoken_edit = QLineEdit()
        self.ngrok_authtoken_edit.setPlaceholderText("可选：ngrok authtoken；已配置客户端时可留空")
        add_secret_row("Auth Token", self.ngrok_authtoken_edit, target_layout=ngrok_layout)
        add_note(ngrok_layout, "使用 ngrok Agent 建立 HTTPS Endpoint，动态 URL 会自动识别。")

        _, tailscale_layout = provider_page("tailscale")
        self.tailscale_executable_selector = ExecutableSelector("Tailscale")
        self.tailscale_executable_edit = self.tailscale_executable_selector.path_edit
        add_config_row(
            "tailscale",
            self.tailscale_executable_selector,
            target_layout=tailscale_layout,
        )
        self._register_executable_selector(
            "tailscale",
            self.tailscale_executable_selector,
        )
        add_note(
            tailscale_layout,
            "使用 Tailscale Funnel HTTPS/443。首次启用可能需要在浏览器批准 Funnel 权限。",
        )

        _, external_layout = provider_page("external")
        self.external_public_url_edit = QLineEdit()
        self.external_public_url_edit.setPlaceholderText("例如 https://mcp.example.com")
        add_config_row("Public URL", self.external_public_url_edit, target_layout=external_layout)
        add_note(
            external_layout,
            "程序不启动隧道进程；你需要自行使用 VPS/Nginx/Caddy/SSH Tunnel 等把该 URL 转发到本机 MCP。",
        )

        self.network_provider_combo.currentIndexChanged.connect(
            self._on_network_provider_changed
        )

        self.remember_secrets = QCheckBox("在这台电脑上保存敏感凭据")
        self.remember_secrets.setChecked(True)
        form_layout.addWidget(self.remember_secrets)
        secret_note = QLabel(
            "敏感凭据保存在用户配置目录并限制文件权限；关闭后不会持久化 OAuth/Cloudflare/ngrok Secret。"
        )
        secret_note.setWordWrap(True)
        secret_note.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        secret_note.setStyleSheet("font-size: 12px;")
        form_layout.addWidget(secret_note)

        # 连接设置属于静态配置区域，不应该随着窗口高度变化被压缩。
        # 固定为自身 sizeHint 高度，把纵向伸缩空间全部留给日志区域。
        self._form_card = form_card
        form_layout.activate()
        form_card.setFixedHeight(form_card.sizeHint().height())
        outer.addWidget(form_card, 0)

        status_card = QFrame()
        status_card.setFrameShape(QFrame.Shape.StyledPanel)
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(20, 16, 20, 16)
        status_layout.setSpacing(10)

        status_top = QHBoxLayout()
        self.status_label = QLabel("●  Stopped")
        self.status_label.setStyleSheet("color: #F56C6C;")
        self.mode_label = QLabel("Quick Tunnel")
        self.mode_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.mode_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
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
        # 日志区域是页面唯一允许随窗口高度变化的主体区域。
        self.logs.setMinimumHeight(72)
        outer.addWidget(self.logs, 1)

        self.about_page = self._build_about_page()
        self.page_stack.addWidget(self.about_page)
        self.home_nav_button.clicked.connect(lambda: self._select_page("home"))
        self.about_nav_button.clicked.connect(lambda: self._select_page("about"))
        self.home_nav_button.setChecked(True)
        self.page_stack.setCurrentWidget(self.home_page)

        # 依据实际布局内容设置最低窗口高度，避免 Qt 在总空间不足时
        # 反向挤压连接设置等静态区域。
        root.adjustSize()
        required_height = root.minimumSizeHint().height()
        self.setMinimumHeight(max(self.minimumHeight(), required_height))

    def _build_about_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(18)

        title = QLabel("关于")
        title_font = QFont()
        title_font.setPointSize(24)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        layout.addWidget(title)

        subtitle = QLabel("Coding Tools MCP 版本与更新信息")
        subtitle.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        layout.addWidget(subtitle)

        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 22, 24, 22)
        card_layout.setSpacing(16)

        app_name = QLabel("Coding Tools MCP")
        app_font = QFont()
        app_font.setPointSize(19)
        app_font.setWeight(QFont.Weight.DemiBold)
        app_name.setFont(app_font)
        card_layout.addWidget(app_name)

        version_row = QHBoxLayout()
        version_row.addWidget(QLabel("当前版本"))
        version_row.addStretch(1)
        self.current_version_label = QLabel(MCP_VERSION)
        version_row.addWidget(self.current_version_label)
        card_layout.addLayout(version_row)

        latest_row = QHBoxLayout()
        latest_row.addWidget(QLabel("GitHub 最新版本"))
        latest_row.addStretch(1)
        self.latest_version_label = QLabel("未检查")
        latest_row.addWidget(self.latest_version_label)
        card_layout.addLayout(latest_row)

        self.update_status_label = QLabel("打开关于页面后会自动检查 GitHub Release。")
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        card_layout.addWidget(self.update_status_label)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.update_button = QPushButton("检查版本")
        self.update_button.setMinimumWidth(120)
        self.update_button.clicked.connect(self._on_update_button_clicked)
        action_row.addWidget(self.update_button)
        card_layout.addLayout(action_row)

        copyright_label = QLabel("Copyright © micromatrix.org")
        copyright_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        card_layout.addWidget(copyright_label)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _select_page(self, page: str) -> None:
        if page == "about":
            self.page_stack.setCurrentWidget(self.about_page)
            self.about_nav_button.setChecked(True)
            if self._release_info is None and not self._checking_update:
                self._check_for_updates_async()
            return
        self.page_stack.setCurrentWidget(self.home_page)
        self.home_nav_button.setChecked(True)

    def _on_update_button_clicked(self) -> None:
        info = self._release_info
        if info is not None and info.update_available:
            target = info.download_url or info.release_url
            if target:
                QDesktopServices.openUrl(QUrl(target))
                return
        self._check_for_updates_async()

    def _check_for_updates_async(self) -> None:
        if self._checking_update:
            return
        self._checking_update = True
        self.update_button.setEnabled(False)
        self.update_button.setText("正在检查…")
        self.update_button.setStyleSheet("")
        self.update_status_label.setText("正在获取 GitHub 最新 Release 信息…")

        def worker() -> None:
            try:
                info = fetch_latest_release(MCP_VERSION)
            except Exception as exc:
                self.bridge.update_check_failed.emit(str(exc))
            else:
                self.bridge.update_checked.emit(info)

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_checked(self, value: object) -> None:
        if not isinstance(value, ReleaseInfo):
            self._on_update_check_failed("GitHub Release 返回了无法识别的数据")
            return
        self._checking_update = False
        self._release_info = value
        self.latest_version_label.setText(value.latest_version)
        self.update_button.setEnabled(True)
        if value.update_available:
            self.update_button.setText("更新")
            self.update_button.setStyleSheet(
                "QPushButton { background-color: #409EFF; color: white; }"
            )
            if value.download_url:
                self.update_status_label.setText(
                    f"发现新版本 {value.latest_version}，点击更新获取 {value.asset_name}。"
                )
            else:
                self.update_status_label.setText(
                    f"发现新版本 {value.latest_version}，但当前 Release 缺少 {value.asset_name}；点击更新打开 Release 页面。"
                )
            return
        self.update_button.setText("检查版本")
        self.update_button.setStyleSheet("")
        self.update_status_label.setText("当前已经是最新版本。")

    def _on_update_check_failed(self, message: str) -> None:
        self._checking_update = False
        self.update_button.setEnabled(True)
        self.update_button.setText("检查版本")
        self.update_button.setStyleSheet("")
        self.update_status_label.setText(f"检查更新失败：{message}")

    def _choose_workspace(self) -> None:
        current = self.workspace_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择 Workspace", current)
        if path:
            self.workspace_edit.setText(path)

    def _refresh_form_card_height(self) -> None:
        if not hasattr(self, "_form_card"):
            return
        layout = self._form_card.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self._form_card.setFixedHeight(self._form_card.sizeHint().height())

    def _on_advanced_oauth_toggled(self, enabled: bool) -> None:
        self.advanced_oauth_panel.setVisible(enabled)
        QTimer.singleShot(0, self._refresh_form_card_height)

    def _choose_frp_config(self) -> None:
        current = self.frp_config_edit.text().strip()
        initial = str(Path(current).expanduser().parent) if current else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 frpc 配置文件",
            initial,
            "FRP Config (*.toml *.yaml *.yml *.json);;All Files (*)",
        )
        if path:
            self.frp_config_edit.setText(path)

    def _register_executable_selector(
        self,
        key: str,
        selector: ExecutableSelector,
    ) -> None:
        self._executable_selectors[key] = selector
        selector.detection_requested.connect(
            lambda key=key: self._detect_executable_async(key, auto_only=True)
        )
        selector.selection_requested.connect(
            lambda key=key: self._choose_executable(key)
        )

    def _choose_executable(self, key: str) -> None:
        selector = self._executable_selectors[key]
        current = selector.configured_path()
        initial = (
            str(Path(current).expanduser().parent)
            if current
            else str(Path.home())
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"选择 {selector.display_name} 客户端",
            initial,
            "All Files (*)",
        )
        if not path:
            return
        selector.set_configured_path(path)
        self._detect_executable_async(key, configured=path)

    def _detect_executable_async(
        self,
        key: str,
        *,
        configured: str = "",
        auto_only: bool = False,
    ) -> None:
        selector = self._executable_selectors[key]
        selector.set_detecting()

        def worker() -> None:
            try:
                candidate = resolve_executable(
                    key,
                    configured=configured,
                    auto_only=auto_only,
                )
            except Exception as exc:
                self.bridge.executable_detection_failed.emit(key, str(exc))
            else:
                self.bridge.executable_detected.emit(key, candidate)

        threading.Thread(target=worker, daemon=True).start()

    def _on_executable_detected(self, key: str, candidate: object) -> None:
        selector = self._executable_selectors.get(key)
        if selector is None:
            return
        selector.set_candidate(candidate)
        self._refresh_network_stack_height()
        self._refresh_form_card_height()

    def _on_executable_detection_failed(self, key: str, message: str) -> None:
        selector = self._executable_selectors.get(key)
        if selector is None:
            return
        selector.set_error(message)
        self._refresh_network_stack_height()
        self._refresh_form_card_height()

    def _selected_network_provider(self) -> str:
        return str(self.network_provider_combo.currentData() or "cloudflare")

    def _refresh_network_stack_height(self) -> None:
        current_page = self.network_stack.currentWidget()
        if current_page is None:
            return
        layout = current_page.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        current_page.adjustSize()
        self.network_stack.setFixedHeight(current_page.sizeHint().height())

    def _on_network_provider_changed(self, _index: int | None = None) -> None:
        provider = self._selected_network_provider()
        index = self._provider_page_indexes.get(provider, 0)
        self.network_stack.setCurrentIndex(index)
        self._refresh_network_stack_height()
        if hasattr(self, "mode_label") and not self.launcher.is_running:
            self.mode_label.setText(self.network_provider_combo.currentText())
        QTimer.singleShot(0, self._refresh_form_card_height)

    def _network_config_from_form(self) -> NetworkConfig:
        provider = self._selected_network_provider()
        if provider == "cloudflare":
            return NetworkConfig(
                provider=provider,
                public_url=self.cf_public_url_edit.text(),
                options={"tunnel_token": self.cf_tunnel_token_edit.text()},
            )
        if provider == "frp":
            return NetworkConfig(
                provider=provider,
                public_url=self.frp_public_url_edit.text(),
                options={
                    "executable": self.frp_executable_edit.text(),
                    "config_file": self.frp_config_edit.text(),
                },
            )
        if provider == "ngrok":
            return NetworkConfig(
                provider=provider,
                public_url=self.ngrok_public_url_edit.text(),
                options={
                    "executable": self.ngrok_executable_edit.text(),
                    "authtoken": self.ngrok_authtoken_edit.text(),
                },
            )
        if provider == "tailscale":
            return NetworkConfig(
                provider=provider,
                options={"executable": self.tailscale_executable_edit.text()},
            )
        return NetworkConfig(
            provider="external",
            public_url=self.external_public_url_edit.text(),
        )

    def _config_from_form(self) -> LaunchConfig:
        advanced_oauth = self.advanced_oauth_toggle.isChecked()
        return LaunchConfig(
            workspace=Path(self.workspace_edit.text().strip()),
            oauth_password=self.password_edit.text(),
            oauth_client_id=self.client_id_edit.text() if advanced_oauth else "",
            oauth_client_secret=self.client_secret_edit.text() if advanced_oauth else "",
            network=self._network_config_from_form(),
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
        self.status_label.setStyleSheet("")
        self.status_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)

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
        self.status_label.setStyleSheet("color: #67C23A;")
        self.start_button.setText("停止 MCP")
        self.start_button.setEnabled(True)
        self._busy = False

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.start_button.setEnabled(True)
        self.start_button.setText("启动 MCP")
        self.status_label.setText("●  Error")
        self.status_label.setStyleSheet("color: #F56C6C;")
        QMessageBox.critical(self, "启动失败", message)

    def _on_stopped(self) -> None:
        self._busy = False
        self.start_button.setEnabled(True)
        self.start_button.setText("启动 MCP")
        self.status_label.setText("●  Stopped")
        self.status_label.setStyleSheet("color: #F56C6C;")
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
        self.advanced_oauth_toggle.setChecked(
            bool(data.get("advanced_oauth_enabled", False))
        )

        raw_network = data.get("network")
        network = raw_network if isinstance(raw_network, dict) else {}
        provider = str(data.get("network_provider") or network.get("provider") or "cloudflare")
        provider_index = self.network_provider_combo.findData(provider)
        self.network_provider_combo.setCurrentIndex(max(0, provider_index))

        cloudflare = network.get("cloudflare") if isinstance(network.get("cloudflare"), dict) else {}
        frp = network.get("frp") if isinstance(network.get("frp"), dict) else {}
        ngrok = network.get("ngrok") if isinstance(network.get("ngrok"), dict) else {}
        tailscale = network.get("tailscale") if isinstance(network.get("tailscale"), dict) else {}
        external = network.get("external") if isinstance(network.get("external"), dict) else {}

        self.cf_public_url_edit.setText(
            str(cloudflare.get("public_url") or data.get("server_url", ""))
        )
        self.frp_public_url_edit.setText(str(frp.get("public_url", "")))
        self.frp_executable_selector.set_configured_path(
            str(frp.get("executable", ""))
        )
        self.frp_config_edit.setText(str(frp.get("config_file", "")))
        self.ngrok_public_url_edit.setText(str(ngrok.get("public_url", "")))
        self.ngrok_executable_selector.set_configured_path(
            str(ngrok.get("executable", ""))
        )
        self.tailscale_executable_selector.set_configured_path(
            str(tailscale.get("executable", ""))
        )
        self.external_public_url_edit.setText(str(external.get("public_url", "")))

        remember = bool(data.get("remember_secrets", True))
        self.remember_secrets.setChecked(remember)
        if remember:
            self.client_secret_edit.setText(str(data.get("client_secret", "")))
            self.password_edit.setText(str(data.get("password", "")))
            self.cf_tunnel_token_edit.setText(
                str(cloudflare.get("tunnel_token") or data.get("tunnel_token", ""))
            )
            self.ngrok_authtoken_edit.setText(str(ngrok.get("authtoken", "")))
        self._on_network_provider_changed()
        self._on_advanced_oauth_toggled(self.advanced_oauth_toggle.isChecked())

    def _save_settings(self) -> None:
        remember = self.remember_secrets.isChecked()
        advanced_oauth = self.advanced_oauth_toggle.isChecked()
        cloudflare: dict[str, object] = {
            "public_url": self.cf_public_url_edit.text().strip(),
        }
        ngrok: dict[str, object] = {
            "public_url": self.ngrok_public_url_edit.text().strip(),
            "executable": self.ngrok_executable_edit.text().strip(),
        }
        if remember:
            cloudflare["tunnel_token"] = self.cf_tunnel_token_edit.text()
            ngrok["authtoken"] = self.ngrok_authtoken_edit.text()

        data: dict[str, object] = {
            "workspace": self.workspace_edit.text().strip(),
            "advanced_oauth_enabled": advanced_oauth,
            "network_provider": self._selected_network_provider(),
            "network": {
                "provider": self._selected_network_provider(),
                "cloudflare": cloudflare,
                "frp": {
                    "public_url": self.frp_public_url_edit.text().strip(),
                    "executable": self.frp_executable_edit.text().strip(),
                    "config_file": self.frp_config_edit.text().strip(),
                },
                "ngrok": ngrok,
                "tailscale": {
                    "executable": self.tailscale_executable_edit.text().strip(),
                },
                "external": {
                    "public_url": self.external_public_url_edit.text().strip(),
                },
            },
            "remember_secrets": remember,
        }
        if advanced_oauth:
            data["client_id"] = self.client_id_edit.text().strip()
        if remember:
            data["password"] = self.password_edit.text()
            if advanced_oauth:
                data["client_secret"] = self.client_secret_edit.text()
        save_settings(data)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_settings()
        if self.launcher.is_running:
            self.launcher.stop()
        event.accept()

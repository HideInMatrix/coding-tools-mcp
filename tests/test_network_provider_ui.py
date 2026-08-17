from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from coding_tools_mcp import __version__ as MCP_VERSION
from coding_tools_launcher.executables.models import ExecutableCandidate
from coding_tools_launcher.ui.main_window import MainWindow
from coding_tools_launcher.updates import ReleaseInfo


class NetworkProviderUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._load_settings_patch = patch(
            "coding_tools_launcher.ui.main_window.load_settings",
            return_value={},
        )
        self._save_settings_patch = patch(
            "coding_tools_launcher.ui.main_window.save_settings",
            return_value=None,
        )
        self._load_settings_patch.start()
        self._save_settings_patch.start()
        self.window = MainWindow()

    def tearDown(self) -> None:
        self.window.close()
        self._save_settings_patch.stop()
        self._load_settings_patch.stop()

    def test_provider_selector_has_five_distinct_modes(self) -> None:
        expected = ["cloudflare", "frp", "ngrok", "tailscale", "external"]
        actual = [
            self.window.network_provider_combo.itemData(index)
            for index in range(self.window.network_provider_combo.count())
        ]
        self.assertEqual(actual, expected)

    def test_sidebar_uses_home_and_about_pages(self) -> None:
        self.assertEqual(self.window.home_nav_button.text(), "首页")
        self.assertEqual(self.window.about_nav_button.text(), "关于")
        self.assertIs(self.window.page_stack.currentWidget(), self.window.home_page)

        with patch.object(self.window, "_check_for_updates_async") as check:
            self.window._select_page("about")

        self.assertIs(self.window.page_stack.currentWidget(), self.window.about_page)
        self.assertTrue(self.window.about_nav_button.isChecked())
        check.assert_called_once()

    def test_about_page_uses_project_version_and_micromatrix_copyright(self) -> None:
        self.assertEqual(self.window.current_version_label.text(), MCP_VERSION)
        copyright_labels = [
            label.text()
            for label in self.window.about_page.findChildren(type(self.window.current_version_label))
        ]
        self.assertIn("Copyright © micromatrix.org", copyright_labels)

    def test_update_available_turns_check_button_into_blue_update_button(self) -> None:
        info = ReleaseInfo(
            current_version=MCP_VERSION,
            latest_version="0.1.4",
            tag_name="v0.1.4",
            release_url="https://github.com/HideInMatrix/coding-tools-mcp/releases/tag/v0.1.4",
            asset_name="Coding-Tools-MCP-windows-arm64.zip",
            download_url=(
                "https://github.com/HideInMatrix/coding-tools-mcp/releases/download/"
                "v0.1.4/Coding-Tools-MCP-windows-arm64.zip"
            ),
            update_available=True,
        )
        self.window._on_update_checked(info)

        self.assertEqual(self.window.latest_version_label.text(), "0.1.4")
        self.assertEqual(self.window.update_button.text(), "更新")
        self.assertIn("#409EFF", self.window.update_button.styleSheet())

    def test_latest_version_keeps_check_version_button(self) -> None:
        info = ReleaseInfo(
            current_version=MCP_VERSION,
            latest_version=MCP_VERSION,
            tag_name=f"v{MCP_VERSION}",
            release_url="https://github.com/HideInMatrix/coding-tools-mcp/releases/latest",
            asset_name="Coding-Tools-MCP-macos-arm64.dmg",
            download_url="",
            update_available=False,
        )
        self.window._on_update_checked(info)

        self.assertEqual(self.window.update_button.text(), "检查版本")
        self.assertEqual(self.window.update_button.styleSheet(), "")

    def test_switching_provider_changes_visible_configuration_page(self) -> None:
        for provider in ("cloudflare", "frp", "ngrok", "tailscale", "external"):
            index = self.window.network_provider_combo.findData(provider)
            self.window.network_provider_combo.setCurrentIndex(index)
            self.assertEqual(
                self.window.network_stack.currentIndex(),
                self.window._provider_page_indexes[provider],
            )
            self.assertEqual(
                self.window.network_stack.height(),
                self.window.network_stack.currentWidget().sizeHint().height(),
            )

    def test_muted_text_uses_system_placeholder_palette_role(self) -> None:
        subtitle = next(
            label
            for label in self.window.findChildren(type(self.window.mode_label))
            if label.text() == "把本地代码目录安全地连接到支持 MCP 的客户端"
        )
        self.assertEqual(
            subtitle.foregroundRole(),
            QPalette.ColorRole.PlaceholderText,
        )
        self.assertEqual(
            self.window.mode_label.foregroundRole(),
            QPalette.ColorRole.PlaceholderText,
        )
        self.assertEqual(
            self.window.ngrok_executable_selector.status_label.foregroundRole(),
            QPalette.ColorRole.PlaceholderText,
        )

    def test_cloudflare_page_uses_compact_vertical_spacing(self) -> None:
        index = self.window.network_provider_combo.findData("cloudflare")
        self.window.network_provider_combo.setCurrentIndex(index)
        page = self.window.network_stack.currentWidget()
        self.assertEqual(page.layout().spacing(), 6)

    def test_provider_pages_share_executable_selector_behavior(self) -> None:
        self.assertEqual(
            set(self.window._executable_selectors),
            {"frpc", "ngrok", "tailscale"},
        )
        for selector in self.window._executable_selectors.values():
            self.assertEqual(selector.configured_path(), "")
            self.assertTrue(selector.detect_button.isEnabled())

    def test_auto_detect_button_uses_shared_resolver_and_updates_status(self) -> None:
        detected = ExecutableCandidate(
            path=Path("/opt/homebrew/bin/ngrok"),
            source="standard",
            version="3.20.0",
        )
        with patch(
            "coding_tools_launcher.ui.main_window.resolve_executable",
            return_value=detected,
        ) as resolver:
            self.window.ngrok_executable_selector.detection_requested.emit()
            deadline = time.monotonic() + 1.0
            while (
                "3.20.0" not in self.window.ngrok_executable_selector.status_label.text()
                and time.monotonic() < deadline
            ):
                QApplication.processEvents()
                time.sleep(0.01)

        resolver.assert_called_once_with(
            "ngrok",
            configured="",
            auto_only=True,
        )
        self.assertIn(
            "/opt/homebrew/bin/ngrok",
            self.window.ngrok_executable_selector.status_label.text(),
        )
        # 自动识别结果仅展示，不固化成手动 override。
        self.assertEqual(self.window.ngrok_executable_selector.configured_path(), "")

    def test_form_builds_provider_specific_network_config(self) -> None:
        self.window.workspace_edit.setText(os.getcwd())
        self.window.password_edit.setText("password")

        index = self.window.network_provider_combo.findData("external")
        self.window.network_provider_combo.setCurrentIndex(index)
        self.window.external_public_url_edit.setText("https://mcp.example.com/mcp")

        config = self.window._config_from_form()
        self.assertEqual(config.network.provider, "external")
        self.assertEqual(config.network.public_url, "https://mcp.example.com")
        self.assertEqual(config.oauth_client_id, "")
        self.assertEqual(config.oauth_client_secret, "")

    def test_advanced_oauth_is_hidden_and_disabled_by_default(self) -> None:
        self.assertFalse(self.window.advanced_oauth_toggle.isChecked())
        self.assertFalse(self.window.advanced_oauth_panel.isVisible())

    def test_disabled_advanced_oauth_ignores_old_client_values(self) -> None:
        self.window.workspace_edit.setText(os.getcwd())
        self.window.password_edit.setText("password")
        self.window.client_id_edit.setText("old-cloudflare-connector-id")
        self.window.client_secret_edit.setText("old-secret")
        index = self.window.network_provider_combo.findData("external")
        self.window.network_provider_combo.setCurrentIndex(index)
        self.window.external_public_url_edit.setText("https://mcp.example.com")

        config = self.window._config_from_form()
        self.assertEqual(config.oauth_client_id, "")
        self.assertEqual(config.oauth_client_secret, "")

    def test_enabled_advanced_oauth_uses_preregistered_client_values(self) -> None:
        self.window.workspace_edit.setText(os.getcwd())
        self.window.password_edit.setText("password")
        self.window.advanced_oauth_toggle.setChecked(True)
        self.window.client_id_edit.setText("manual-client")
        self.window.client_secret_edit.setText("manual-secret")
        index = self.window.network_provider_combo.findData("external")
        self.window.network_provider_combo.setCurrentIndex(index)
        self.window.external_public_url_edit.setText("https://mcp.example.com")

        config = self.window._config_from_form()
        self.assertEqual(config.oauth_client_id, "manual-client")
        self.assertEqual(config.oauth_client_secret, "manual-secret")

    def test_legacy_saved_client_id_does_not_enable_advanced_oauth(self) -> None:
        self.window.close()
        with patch(
            "coding_tools_launcher.ui.main_window.load_settings",
            return_value={
                "client_id": "cloudflare-connector-id-from-old-version",
                "client_secret": "old-secret",
                "remember_secrets": True,
            },
        ):
            legacy_window = MainWindow()
            try:
                self.assertFalse(legacy_window.advanced_oauth_toggle.isChecked())
                self.assertEqual(
                    legacy_window.client_id_edit.text(),
                    "cloudflare-connector-id-from-old-version",
                )
            finally:
                legacy_window.close()


if __name__ == "__main__":
    unittest.main()

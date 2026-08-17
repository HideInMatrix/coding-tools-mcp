from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from coding_tools_launcher.updates import (
    fetch_latest_release,
    is_newer_version,
    platform_asset_name,
)
from scripts.package_release import platform_label, release_base_name


class UpdateNamingTests(unittest.TestCase):
    def test_version_comparison(self) -> None:
        self.assertTrue(is_newer_version("0.1.4", "0.1.0"))
        self.assertTrue(is_newer_version("v0.2.0", "0.1.9"))
        self.assertFalse(is_newer_version("0.1.0", "0.1.0"))
        self.assertFalse(is_newer_version("0.0.9", "0.1.0"))

    def test_release_asset_names_are_versionless_and_normalized(self) -> None:
        expected = {
            ("Windows", "AMD64"): "Coding-Tools-MCP-windows-x64.zip",
            ("Windows", "ARM64"): "Coding-Tools-MCP-windows-arm64.zip",
            ("Darwin", "x86_64"): "Coding-Tools-MCP-macos-x64.dmg",
            ("Darwin", "arm64"): "Coding-Tools-MCP-macos-arm64.dmg",
            ("Linux", "x86_64"): "Coding-Tools-MCP-linux-x64.tar.gz",
            ("Linux", "aarch64"): "Coding-Tools-MCP-linux-arm64.tar.gz",
        }
        for (system, machine), filename in expected.items():
            with self.subTest(system=system, machine=machine):
                self.assertEqual(
                    platform_asset_name(system=system, machine=machine),
                    filename,
                )
                self.assertNotIn("0.1.0", filename)

    def test_package_platform_labels_match_updater_names(self) -> None:
        # package_release uses the same normalized label vocabulary consumed
        # by the About-page updater: windows/macos/linux + x64/arm64.
        self.assertNotIn("intel", platform_label())
        self.assertNotIn("apple-silicon", platform_label())

    def test_release_base_name_never_contains_version(self) -> None:
        with patch("scripts.package_release.platform_label", return_value="windows-arm64"):
            self.assertEqual(
                release_base_name(),
                "Coding-Tools-MCP-windows-arm64",
            )

    def test_latest_release_uses_exact_platform_asset(self) -> None:
        payload = {
            "tag_name": "v0.1.4",
            "html_url": "https://github.com/HideInMatrix/coding-tools-mcp/releases/tag/v0.1.4",
            "assets": [
                {
                    "name": "Coding-Tools-MCP-windows-arm64.zip",
                    "browser_download_url": (
                        "https://github.com/HideInMatrix/coding-tools-mcp/releases/download/"
                        "v0.1.4/Coding-Tools-MCP-windows-arm64.zip"
                    ),
                }
            ],
        }
        response = io.BytesIO(json.dumps(payload).encode("utf-8"))
        with (
            patch(
                "coding_tools_launcher.updates.platform_asset_name",
                return_value="Coding-Tools-MCP-windows-arm64.zip",
            ),
            patch(
                "coding_tools_launcher.updates.urllib.request.urlopen",
                return_value=response,
            ),
        ):
            info = fetch_latest_release("0.1.0")

        self.assertTrue(info.update_available)
        self.assertEqual(info.latest_version, "0.1.4")
        self.assertEqual(info.asset_name, "Coding-Tools-MCP-windows-arm64.zip")
        self.assertTrue(info.download_url.endswith("/Coding-Tools-MCP-windows-arm64.zip"))


if __name__ == "__main__":
    unittest.main()

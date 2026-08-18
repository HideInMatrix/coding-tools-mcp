from __future__ import annotations

import io
import http.client
import json
import unittest
from unittest.mock import patch

from build_desktop import MACOS_BUNDLE_IDENTIFIER, resolve_build_version
from coding_tools_launcher.self_update import _parse_checksum
from coding_tools_launcher.version import DEV_VERSION, git_release_version
from coding_tools_launcher.updates import (
    DEFAULT_GITHUB_DOWNLOAD_PROXY,
    apply_download_proxy,
    fetch_latest_release,
    is_newer_version,
    normalize_download_proxy_prefix,
    platform_asset_name,
    updater_asset_name,
)
from scripts.package_release import platform_label, release_base_name


class UpdateNamingTests(unittest.TestCase):
    def test_download_proxy_prefix_is_normalized_and_can_be_disabled(self) -> None:
        self.assertEqual(
            normalize_download_proxy_prefix("https://mirror.example.com/base"),
            "https://mirror.example.com/base/",
        )
        self.assertEqual(normalize_download_proxy_prefix(""), "")
        with self.assertRaisesRegex(ValueError, "http/https"):
            normalize_download_proxy_prefix("file:///tmp/proxy")
        with self.assertRaisesRegex(ValueError, "查询参数"):
            normalize_download_proxy_prefix("https://mirror.example.com/?token=x")

    def test_download_proxy_only_rewrites_github_assets(self) -> None:
        github_url = "https://github.com/org/repo/releases/download/v1/app.zip"
        self.assertEqual(
            apply_download_proxy(github_url, "https://mirror.example.com"),
            "https://mirror.example.com/" + github_url,
        )
        external = "https://downloads.example.com/app.zip"
        self.assertEqual(
            apply_download_proxy(external, "https://mirror.example.com"),
            external,
        )

    def test_release_tag_becomes_desktop_build_version(self) -> None:
        with patch.dict(
            "os.environ",
            {"GITHUB_REF_NAME": "v0.1.4"},
            clear=False,
        ):
            self.assertEqual(resolve_build_version(), "0.1.4")

    def test_explicit_build_version_has_priority(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CODING_TOOLS_RELEASE_VERSION": "v1.2.3",
                "GITHUB_REF_NAME": "v0.1.4",
            },
            clear=False,
        ):
            self.assertEqual(resolve_build_version(), "1.2.3")

    def test_build_version_does_not_use_mcp_core_version(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "CODING_TOOLS_RELEASE_VERSION": "",
                    "GITHUB_REF_NAME": "main",
                },
                clear=False,
            ),
            patch("build_desktop.git_release_version", return_value=None),
        ):
            self.assertEqual(resolve_build_version(), DEV_VERSION)

    def test_git_release_version_reads_exact_head_tag(self) -> None:
        completed = __import__("subprocess").CompletedProcess(
            args=[],
            returncode=0,
            stdout="v0.1.5\n",
            stderr="",
        )
        with patch("coding_tools_launcher.version.subprocess.run", return_value=completed):
            self.assertEqual(git_release_version(), "0.1.5")

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

    def test_updater_asset_names_use_extractable_archives(self) -> None:
        self.assertEqual(
            updater_asset_name(system="Darwin", machine="arm64"),
            "Coding-Tools-MCP-macos-arm64.zip",
        )
        self.assertEqual(
            updater_asset_name(system="Windows", machine="AMD64"),
            "Coding-Tools-MCP-windows-x64.zip",
        )
        self.assertEqual(
            updater_asset_name(system="Linux", machine="x86_64"),
            "Coding-Tools-MCP-linux-x64.tar.gz",
        )

    def test_macos_bundle_identifier_is_stable(self) -> None:
        self.assertEqual(
            MACOS_BUNDLE_IDENTIFIER,
            "org.micromatrix.coding-tools-mcp",
        )

    def test_update_checksum_requires_matching_filename(self) -> None:
        digest = "a" * 64
        self.assertEqual(
            _parse_checksum(
                f"{digest}  Coding-Tools-MCP-macos-arm64.zip\n",
                "Coding-Tools-MCP-macos-arm64.zip",
            ),
            digest,
        )
        with self.assertRaisesRegex(ValueError, "不匹配"):
            _parse_checksum(
                f"{digest}  Coding-Tools-MCP-macos-x64.zip\n",
                "Coding-Tools-MCP-macos-arm64.zip",
            )

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
                },
                {
                    "name": "Coding-Tools-MCP-windows-arm64.zip.sha256",
                    "browser_download_url": (
                        "https://github.com/HideInMatrix/coding-tools-mcp/releases/download/"
                        "v0.1.4/Coding-Tools-MCP-windows-arm64.zip.sha256"
                    ),
                },
            ],
        }
        response = io.BytesIO(json.dumps(payload).encode("utf-8"))
        with (
            patch(
                "coding_tools_launcher.updates.platform_asset_name",
                return_value="Coding-Tools-MCP-windows-arm64.zip",
            ),
            patch(
                "coding_tools_launcher.updates.updater_asset_name",
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
        self.assertEqual(info.update_asset_name, "Coding-Tools-MCP-windows-arm64.zip")
        self.assertTrue(info.update_download_url.endswith("/Coding-Tools-MCP-windows-arm64.zip"))
        self.assertTrue(info.checksum_url.endswith(".zip.sha256"))
        self.assertTrue(info.download_url.startswith(DEFAULT_GITHUB_DOWNLOAD_PROXY))
        self.assertTrue(info.update_download_url.startswith(DEFAULT_GITHUB_DOWNLOAD_PROXY))
        self.assertTrue(info.checksum_url.startswith(DEFAULT_GITHUB_DOWNLOAD_PROXY))

    def test_latest_release_retries_incomplete_read(self) -> None:
        payload = {
            "tag_name": "v0.2.3",
            "html_url": "https://github.com/HideInMatrix/coding-tools-mcp/releases/tag/v0.2.3",
            "assets": [],
        }
        good_response = io.BytesIO(json.dumps(payload).encode("utf-8"))
        incomplete = http.client.IncompleteRead(b'{"tag_name":"v0.2', 10)
        with (
            patch(
                "coding_tools_launcher.updates.platform_asset_name",
                return_value="Coding-Tools-MCP-windows-arm64.zip",
            ),
            patch(
                "coding_tools_launcher.updates.urllib.request.urlopen",
                side_effect=[incomplete, good_response],
            ) as urlopen,
            patch("coding_tools_launcher.updates.time.sleep") as sleep,
        ):
            info = fetch_latest_release("0.2.2")

        self.assertEqual(info.latest_version, "0.2.3")
        self.assertTrue(info.update_available)
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

    def test_latest_release_reports_readable_error_after_retries(self) -> None:
        incomplete = http.client.IncompleteRead(b"partial", 100)
        with (
            patch(
                "coding_tools_launcher.updates.urllib.request.urlopen",
                side_effect=incomplete,
            ),
            patch("coding_tools_launcher.updates.time.sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "网络响应不完整.*自动重试 3 次"):
                fetch_latest_release("0.2.2")


if __name__ == "__main__":
    unittest.main()

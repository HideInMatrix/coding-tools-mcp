from __future__ import annotations

import io
import http.client
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_desktop import MACOS_BUNDLE_IDENTIFIER, resolve_build_version
import agent_workbench.self_update as self_update
from agent_workbench.self_update import _parse_checksum
from agent_workbench.version import DEV_VERSION, git_release_version
from agent_workbench.updates import (
    DEFAULT_GITHUB_DOWNLOAD_PROXY,
    apply_download_proxy,
    fetch_latest_release,
    is_newer_version,
    normalize_download_proxy_prefix,
    platform_asset_name,
    updater_asset_name,
)
import scripts.package_release as package_release
from scripts.package_release import _create_macos_dmg, platform_label, release_base_name


class UpdateNamingTests(unittest.TestCase):
    def test_windows_release_package_is_inno_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "dist"
            source = dist / "MicroMatrix Workbench"
            source.mkdir(parents=True)
            (source / "MicroMatrix Workbench.exe").write_bytes(b"onedir-executable")
            output_base = root / "release" / "MicroMatrix-Workbench-windows-x64"
            output_base.parent.mkdir()

            def build_installer(_command: list[str], **_kwargs: object) -> None:
                Path(f"{output_base}.exe").write_bytes(b"inno-installer")

            with (
                patch.object(package_release, "DIST_DIR", dist),
                patch.object(package_release, "INNO_SCRIPT", root / "installer.iss"),
                patch.object(package_release, "_resolve_inno_compiler", return_value=Path("ISCC.exe")),
                patch.object(package_release, "_inno_architecture", return_value="x64compatible"),
                patch.object(package_release.subprocess, "run", side_effect=build_installer) as run,
            ):
                package_release.INNO_SCRIPT.write_text("[Setup]\n", encoding="utf-8")
                packages = package_release.package_windows(output_base, version="1.2.3")

            self.assertEqual(packages, [Path(f"{output_base}.exe")])
            self.assertEqual(packages[0].read_bytes(), b"inno-installer")
            environment = run.call_args.kwargs["env"]
            self.assertEqual(environment["MICROMATRIX_WORKBENCH_INSTALLER_VERSION"], "1.2.3")
            self.assertEqual(environment["MICROMATRIX_WORKBENCH_INSTALLER_ARCH"], "x64compatible")

    def test_windows_updater_replaces_current_executable_not_install_directory(self) -> None:
        executable = Path("C:/Portable/MicroMatrix Workbench.exe")
        with (
            patch.object(self_update, "is_frozen", return_value=True),
            patch.object(self_update.sys, "platform", "win32"),
            patch.object(self_update.sys, "executable", str(executable)),
        ):
            self.assertEqual(self_update.current_install_target(), executable.resolve())

    def test_windows_update_launches_installer_instead_of_replacing_itself(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            installer = Path(temporary) / "MicroMatrix-Workbench-windows-x64.exe"
            installer.write_bytes(b"installer")
            with patch("agent_workbench.self_update.subprocess.Popen") as popen:
                self_update._spawn_windows_installer(installer)

        command = popen.call_args.args[0]
        self.assertEqual(command[0], str(installer))
        self.assertIn("/VERYSILENT", command)
        self.assertIn("/CLOSEAPPLICATIONS", command)
        self.assertNotIn("powershell.exe", " ".join(command).lower())

    def test_macos_dmg_creation_retries_resource_busy_at_fresh_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            staging.mkdir()
            image = root / "release" / "MicroMatrix-Workbench-macos-arm64.dmg"
            commands: list[list[str]] = []

            def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                if len(commands) == 1:
                    raise subprocess.CalledProcessError(
                        1,
                        command,
                        stderr="hdiutil: create failed - Resource busy",
                    )
                Path(command[-1]).write_bytes(b"valid-dmg")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("scripts.package_release.subprocess.run", side_effect=run),
                patch("scripts.package_release.time.sleep") as sleep,
                patch("scripts.package_release.os.sync", create=True),
            ):
                _create_macos_dmg(staging, image, attempts=3)

            self.assertEqual(image.read_bytes(), b"valid-dmg")
            self.assertEqual(len(commands), 2)
            self.assertNotEqual(commands[0][-1], commands[1][-1])
            sleep.assert_called_once_with(2.0)

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
                "MICROMATRIX_WORKBENCH_RELEASE_VERSION": "v1.2.3",
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
                    "MICROMATRIX_WORKBENCH_RELEASE_VERSION": "",
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
        with patch("agent_workbench.version.subprocess.run", return_value=completed):
            self.assertEqual(git_release_version(), "0.1.5")

    def test_version_comparison(self) -> None:
        self.assertTrue(is_newer_version("0.1.4", "0.1.0"))
        self.assertTrue(is_newer_version("v0.2.0", "0.1.9"))
        self.assertFalse(is_newer_version("0.1.0", "0.1.0"))
        self.assertFalse(is_newer_version("0.0.9", "0.1.0"))

    def test_release_asset_names_are_versionless_and_normalized(self) -> None:
        expected = {
            ("Windows", "AMD64"): "MicroMatrix-Workbench-windows-x64.exe",
            ("Windows", "ARM64"): "MicroMatrix-Workbench-windows-arm64.exe",
            ("Darwin", "x86_64"): "MicroMatrix-Workbench-macos-x64.dmg",
            ("Darwin", "arm64"): "MicroMatrix-Workbench-macos-arm64.dmg",
            ("Linux", "x86_64"): "MicroMatrix-Workbench-linux-x64.tar.gz",
            ("Linux", "aarch64"): "MicroMatrix-Workbench-linux-arm64.tar.gz",
        }
        for (system, machine), filename in expected.items():
            with self.subTest(system=system, machine=machine):
                self.assertEqual(
                    platform_asset_name(system=system, machine=machine),
                    filename,
                )
                self.assertNotIn("0.1.0", filename)

    def test_updater_asset_names_use_platform_update_packages(self) -> None:
        self.assertEqual(
            updater_asset_name(system="Darwin", machine="arm64"),
            "MicroMatrix-Workbench-macos-arm64.zip",
        )
        self.assertEqual(
            updater_asset_name(system="Windows", machine="AMD64"),
            "MicroMatrix-Workbench-windows-x64.exe",
        )
        self.assertEqual(
            updater_asset_name(system="Linux", machine="x86_64"),
            "MicroMatrix-Workbench-linux-x64.tar.gz",
        )

    def test_macos_bundle_identifier_is_stable(self) -> None:
        self.assertEqual(
            MACOS_BUNDLE_IDENTIFIER,
            "org.micromatrix.workbench",
        )

    def test_update_checksum_requires_matching_filename(self) -> None:
        digest = "a" * 64
        self.assertEqual(
            _parse_checksum(
                f"{digest}  MicroMatrix-Workbench-macos-arm64.zip\n",
                "MicroMatrix-Workbench-macos-arm64.zip",
            ),
            digest,
        )
        with self.assertRaisesRegex(ValueError, "不匹配"):
            _parse_checksum(
                f"{digest}  MicroMatrix-Workbench-macos-x64.zip\n",
                "MicroMatrix-Workbench-macos-arm64.zip",
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
                "MicroMatrix-Workbench-windows-arm64",
            )

    def test_latest_release_uses_exact_platform_asset(self) -> None:
        payload = {
            "tag_name": "v0.1.4",
            "html_url": "https://github.com/HideInMatrix/micromatrix-workbench/releases/tag/v0.1.4",
            "assets": [
                {
                    "name": "MicroMatrix-Workbench-windows-arm64.exe",
                    "browser_download_url": (
                        "https://github.com/HideInMatrix/micromatrix-workbench/releases/download/"
                        "v0.1.4/MicroMatrix-Workbench-windows-arm64.exe"
                    ),
                },
                {
                    "name": "MicroMatrix-Workbench-windows-arm64.exe.sha256",
                    "browser_download_url": (
                        "https://github.com/HideInMatrix/micromatrix-workbench/releases/download/"
                        "v0.1.4/MicroMatrix-Workbench-windows-arm64.exe.sha256"
                    ),
                },
            ],
        }
        response = io.BytesIO(json.dumps(payload).encode("utf-8"))
        with (
            patch(
                "agent_workbench.updates.platform_asset_name",
                return_value="MicroMatrix-Workbench-windows-arm64.exe",
            ),
            patch(
                "agent_workbench.updates.updater_asset_name",
                return_value="MicroMatrix-Workbench-windows-arm64.exe",
            ),
            patch(
                "agent_workbench.updates.urllib.request.urlopen",
                return_value=response,
            ),
        ):
            info = fetch_latest_release("0.1.0")

        self.assertTrue(info.update_available)
        self.assertEqual(info.latest_version, "0.1.4")
        self.assertEqual(info.asset_name, "MicroMatrix-Workbench-windows-arm64.exe")
        self.assertTrue(info.download_url.endswith("/MicroMatrix-Workbench-windows-arm64.exe"))
        self.assertEqual(info.update_asset_name, "MicroMatrix-Workbench-windows-arm64.exe")
        self.assertTrue(info.update_download_url.endswith("/MicroMatrix-Workbench-windows-arm64.exe"))
        self.assertTrue(info.checksum_url.endswith(".exe.sha256"))
        self.assertTrue(info.download_url.startswith(DEFAULT_GITHUB_DOWNLOAD_PROXY))
        self.assertTrue(info.update_download_url.startswith(DEFAULT_GITHUB_DOWNLOAD_PROXY))
        self.assertTrue(info.checksum_url.startswith(DEFAULT_GITHUB_DOWNLOAD_PROXY))

    def test_latest_release_retries_incomplete_read(self) -> None:
        payload = {
            "tag_name": "v0.2.3",
            "html_url": "https://github.com/HideInMatrix/micromatrix-workbench/releases/tag/v0.2.3",
            "assets": [],
        }
        good_response = io.BytesIO(json.dumps(payload).encode("utf-8"))
        incomplete = http.client.IncompleteRead(b'{"tag_name":"v0.2', 10)
        with (
            patch(
                "agent_workbench.updates.platform_asset_name",
                return_value="MicroMatrix-Workbench-windows-arm64.exe",
            ),
            patch(
                "agent_workbench.updates.urllib.request.urlopen",
                side_effect=[incomplete, good_response],
            ) as urlopen,
            patch("agent_workbench.updates.time.sleep") as sleep,
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
                "agent_workbench.updates.urllib.request.urlopen",
                side_effect=incomplete,
            ),
            patch("agent_workbench.updates.time.sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "网络响应不完整.*自动重试 3 次"):
                fetch_latest_release("0.2.2")


if __name__ == "__main__":
    unittest.main()

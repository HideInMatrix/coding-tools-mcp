from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import build_desktop


class DesktopBuildFrontendArtifactTests(unittest.TestCase):
    def test_desktop_bundles_use_onedir(self) -> None:
        self.assertEqual(build_desktop.pyinstaller_bundle_mode("win32"), "--onedir")
        self.assertEqual(build_desktop.pyinstaller_bundle_mode("cygwin"), "--onedir")
        self.assertEqual(build_desktop.pyinstaller_bundle_mode("darwin"), "--onedir")

    def test_release_web_build_is_standardized_on_npm(self) -> None:
        with (
            patch.object(build_desktop.shutil, "which", return_value="/usr/bin/npm") as which,
            patch.object(build_desktop.subprocess, "check_call") as check_call,
            patch.object(Path, "is_file", return_value=False),
        ):
            build_desktop.build_web_frontend()

        which.assert_called_once_with("npm")
        self.assertEqual(
            check_call.call_args_list,
            [
                call(
                    [
                        "/usr/bin/npm",
                        "install",
                        "--no-package-lock",
                        "--no-audit",
                        "--no-fund",
                    ],
                    cwd=build_desktop.DEFAULT_WEB_DIR,
                ),
                call(
                    ["/usr/bin/npm", "run", "build"],
                    cwd=build_desktop.DEFAULT_WEB_DIR,
                ),
            ],
        )

    def test_resolve_web_dist_accepts_reusable_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dist = Path(temp_dir) / "web-dist"
            dist.mkdir()
            (dist / "index.html").write_text("<html></html>", encoding="utf-8")

            self.assertEqual(build_desktop.resolve_web_dist(str(dist)), dist.resolve())

    def test_resolve_web_dist_rejects_missing_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(SystemExit) as raised:
                build_desktop.resolve_web_dist(temp_dir)

        self.assertIn("找不到前端构建产物", str(raised.exception))

    def test_build_web_and_external_dist_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit):
            build_desktop.parse_args(["--build-web", "--web-dist", "/tmp/web-dist"])


if __name__ == "__main__":
    unittest.main()

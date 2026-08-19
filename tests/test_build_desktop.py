from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import build_desktop


class DesktopBuildFrontendArtifactTests(unittest.TestCase):
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

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_tools_launcher import version


class CurrentVersionTests(unittest.TestCase):
    def test_reads_packaged_build_version_from_resource_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_dir = root / "coding_tools_launcher"
            metadata_dir.mkdir(parents=True)
            (metadata_dir / version.BUILD_VERSION_FILENAME).write_text(
                "0.2.2\n", encoding="utf-8"
            )

            with patch.object(version, "resource_root", return_value=root):
                self.assertEqual(version.current_version(), "0.2.2")

    def test_invalid_packaged_version_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_dir = root / "coding_tools_launcher"
            metadata_dir.mkdir(parents=True)
            (metadata_dir / version.BUILD_VERSION_FILENAME).write_text(
                "not-a-version\n", encoding="utf-8"
            )

            with (
                patch.object(version, "resource_root", return_value=root),
                patch.object(version.Path, "resolve", return_value=root / "missing.py"),
                patch.object(version, "git_release_version", return_value=None),
            ):
                self.assertEqual(version.current_version(), version.DEV_VERSION)


if __name__ == "__main__":
    unittest.main()

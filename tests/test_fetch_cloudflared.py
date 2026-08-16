from __future__ import annotations

import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts.fetch_cloudflared import download_first_available, target_candidates


class CloudflaredTargetTests(unittest.TestCase):
    def test_windows_arm64_prefers_native_then_amd64_fallback(self) -> None:
        assets, platform_dir, executable = target_candidates("windows", "arm64")

        self.assertEqual(
            assets,
            [
                "cloudflared-windows-arm64.exe",
                "cloudflared-windows-amd64.exe",
            ],
        )
        self.assertEqual(platform_dir, "windows-arm64")
        self.assertEqual(executable, "cloudflared.exe")

    def test_other_platforms_have_single_native_asset(self) -> None:
        self.assertEqual(
            target_candidates("darwin", "arm64")[0],
            ["cloudflared-darwin-arm64.tgz"],
        )
        self.assertEqual(
            target_candidates("linux", "x86_64")[0],
            ["cloudflared-linux-amd64"],
        )

    def test_404_uses_next_candidate(self) -> None:
        attempts: list[str] = []

        def fake_urlretrieve(url: str, destination: Path):
            attempts.append(url)
            if url.endswith("cloudflared-windows-arm64.exe"):
                raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
            Path(destination).write_bytes(b"cloudflared")
            return str(destination), None

        with tempfile.TemporaryDirectory() as temporary:
            with patch("scripts.fetch_cloudflared.urllib.request.urlretrieve", fake_urlretrieve):
                asset, path = download_first_available(
                    [
                        "cloudflared-windows-arm64.exe",
                        "cloudflared-windows-amd64.exe",
                    ],
                    Path(temporary),
                )

            self.assertEqual(asset, "cloudflared-windows-amd64.exe")
            self.assertEqual(path.read_bytes(), b"cloudflared")

        self.assertEqual(len(attempts), 2)


if __name__ == "__main__":
    unittest.main()

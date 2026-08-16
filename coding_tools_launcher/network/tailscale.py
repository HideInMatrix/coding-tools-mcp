from __future__ import annotations

import re
import subprocess

from ..config import NetworkConfig, normalize_server_url
from ..process_utils import LogCallback, hidden_process_kwargs
from .base import NetworkProviderResult
from .process import ProcessNetworkProvider


TAILSCALE_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9.-]+\.ts\.net")


class TailscaleProvider(ProcessNetworkProvider):
    key = "tailscale"
    display_name = "Tailscale Funnel"
    process_name = "tailscale funnel"

    def __init__(self, log: LogCallback):
        super().__init__(log)
        self._executable = None

    def start(self, host: str, port: int, config: NetworkConfig) -> NetworkProviderResult:
        executable = self.resolve_executable(config.options.get("executable", ""), "tailscale")
        self._executable = executable
        target = f"http://127.0.0.1:{port}"
        command = [str(executable), "funnel", "--yes", "--https=443", target]
        self.spawn(command, prefix="tailscale")
        line = self.wait_for_line(
            lambda value: bool(TAILSCALE_URL_PATTERN.search(value)),
            timeout=25.0,
            description="Tailscale Funnel URL",
        )
        match = TAILSCALE_URL_PATTERN.search(line)
        assert match is not None
        public_url = normalize_server_url(match.group(0))
        return NetworkProviderResult(self.key, public_url, "Tailscale Funnel")

    def stop(self) -> None:
        super().stop()
        if self._executable is None:
            return
        # The provider owns HTTPS/443 Funnel while active. Mirror the command's
        # original flags and append `off`, per the Tailscale CLI contract.
        try:
            subprocess.run(
                [str(self._executable), "funnel", "--https=443", "off"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                **hidden_process_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._executable = None

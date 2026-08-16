from __future__ import annotations

import json
import re

from ..config import NetworkConfig, normalize_server_url
from ..process_utils import LogCallback
from .base import NetworkProviderResult
from .process import ProcessNetworkProvider


URL_PATTERN = re.compile(r"https://[^\s\"']+")


class NgrokProvider(ProcessNetworkProvider):
    key = "ngrok"
    display_name = "ngrok"
    process_name = "ngrok"

    def __init__(self, log: LogCallback):
        super().__init__(log)
        self._detected_url = ""

    def _extract_url(self, line: str) -> str:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            for key in ("url", "addr", "endpoint"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.startswith("https://"):
                    return candidate
            message = value.get("msg")
            if isinstance(message, str):
                match = URL_PATTERN.search(message)
                if match:
                    return match.group(0)
        match = URL_PATTERN.search(line)
        return match.group(0) if match else ""

    def start(self, host: str, port: int, config: NetworkConfig) -> NetworkProviderResult:
        executable = self.resolve_executable(config.options.get("executable", ""), "ngrok")
        command = [
            str(executable),
            "http",
            f"http://{host}:{port}",
            "--log",
            "stdout",
            "--log-format",
            "json",
        ]
        authtoken = config.options.get("authtoken", "").strip()
        if authtoken:
            command.extend(["--authtoken", authtoken])
        if config.public_url:
            command.extend(["--url", config.public_url])
        self.spawn(command, prefix="ngrok")

        if config.public_url:
            self.wait_until_stable()
            public_url = config.public_url
        else:
            self.wait_for_line(
                lambda line: bool(self._capture_url(line)),
                timeout=20.0,
                description="ngrok 公网 URL",
            )
            public_url = normalize_server_url(self._detected_url)
        return NetworkProviderResult(self.key, public_url, "ngrok")

    def _capture_url(self, line: str) -> bool:
        url = self._extract_url(line)
        if url:
            self._detected_url = url
            return True
        return False

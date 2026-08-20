from __future__ import annotations

import os
from typing import Any

from ...errors import ToolError


class ToolchainHandlers:
    """Toolchain discovery handlers backed by the runtime resolver state."""

    def discover_toolchains(self, args: dict[str, Any]) -> dict[str, Any]:
        kinds = [
            str(item)
            for item in list(args.get("kinds") or ["node", "python", "go"])
        ]
        privileged = self.permission_mode == "dangerous" or self._permission_granted(
            "privileged_executable"
        )
        discovered = self.toolchains.discover(kinds, privileged=privileged)
        toolchains = discovered.get("toolchains")
        missing = [
            kind
            for kind in kinds
            if not isinstance(toolchains, dict)
            or not isinstance(toolchains.get(kind), dict)
            or toolchains[kind].get("selected") is None
        ]
        if privileged:
            for kind in kinds:
                if kind in missing:
                    self._privileged_toolchain_misses.add(kind)
                else:
                    self._privileged_toolchain_misses.discard(kind)

        unresolved_without_host_retry = [
            kind for kind in missing if kind not in self._privileged_toolchain_misses
        ]
        if unresolved_without_host_retry and not privileged:
            raise ToolError(
                "PERMISSION_REQUIRED",
                "沙箱环境中未找到请求的工具链；需要读取用户登录环境后重试。",
                "permission",
                False,
                {
                    "permission": "privileged_executable",
                    "missing": unresolved_without_host_retry,
                    "sandbox_path": list(self.safe_exec_path),
                },
            )
        return {
            **discovered,
            "shell_startup_files_evaluated": privileged and os.name != "nt",
            "home_scanned_recursively": False,
            "elevated_user_environment_queried": privileged,
            "host_lookup_exhausted": sorted(
                kind for kind in missing if kind in self._privileged_toolchain_misses
            ),
        }

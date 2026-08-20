from __future__ import annotations

from ..core.registry import ToolRegistry
from .filesystem.definitions import FILESYSTEM_TOOLS
from .git.definitions import GIT_TOOLS
from .process.definitions import PROCESS_TOOLS
from .system.definitions import SYSTEM_TOOLS
from .toolchains.definitions import TOOLCHAIN_TOOLS
from .workbench.definitions import WORKBENCH_TOOLS


def build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many(SYSTEM_TOOLS)
    registry.register_many(TOOLCHAIN_TOOLS)
    registry.register_many(FILESYSTEM_TOOLS)
    registry.register_many(PROCESS_TOOLS)
    registry.register_many(GIT_TOOLS)
    registry.register_many(WORKBENCH_TOOLS)
    return registry


__all__ = ["build_tool_registry"]

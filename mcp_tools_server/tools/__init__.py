from __future__ import annotations

from ..core import ToolRegistry
from .filesystem import FILESYSTEM_TOOLS
from .git import GIT_TOOLS
from .process import PROCESS_TOOLS
from .system import SYSTEM_TOOLS
from .toolchains import TOOLCHAIN_TOOLS


def build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many(SYSTEM_TOOLS)
    registry.register_many(TOOLCHAIN_TOOLS)
    registry.register_many(FILESYSTEM_TOOLS)
    registry.register_many(PROCESS_TOOLS)
    registry.register_many(GIT_TOOLS)
    return registry


__all__ = ["build_tool_registry"]

from __future__ import annotations

import re
from enum import StrEnum


WORKBENCH_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class ResourceScope(StrEnum):
    BUILTIN = "built-in"
    GLOBAL = "global"
    WORKSPACE = "workspace"


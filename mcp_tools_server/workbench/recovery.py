from __future__ import annotations

import logging
import os
import time
from pathlib import Path


LOGGER = logging.getLogger(__name__)


def should_quarantine_error(error: BaseException) -> bool:
    """Keep future-version data in place so older binaries never mutate it."""

    return "unsupported future" not in str(error).lower()


def quarantine_path(path: Path, *, reason: str) -> Path | None:
    """Move one corrupt catalog entry aside instead of failing every refresh.

    Quarantine lives beside the affected catalog and never follows symlinks.
    The original bytes remain available for diagnostics/recovery.
    """

    if not path.exists() and not path.is_symlink():
        return None
    quarantine = path.parent / ".quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    destination = quarantine / path.name
    if destination.exists() or destination.is_symlink():
        destination = quarantine / f"{path.stem}-{time.time_ns()}{path.suffix}"
    try:
        os.replace(path, destination)
    except OSError as exc:
        LOGGER.warning("Failed to quarantine corrupt Workbench asset %s: %s", path, exc)
        return None
    LOGGER.warning(
        "Quarantined corrupt Workbench asset %s -> %s: %s",
        path,
        destination,
        reason,
    )
    return destination

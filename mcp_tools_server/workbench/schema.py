from __future__ import annotations

from collections.abc import Mapping
from typing import Any


CURRENT_WORKBENCH_SCHEMA_VERSION = 1


def validate_workbench_schema(
    value: Mapping[str, Any],
    *,
    resource_type: str,
) -> dict[str, Any]:
    """Validate a Workbench payload against the current persisted schema.

    Workbench 0.3.x has not been released, so pre-release prototype formats are
    intentionally not supported. Future versions are rejected rather than
    guessed so older binaries cannot silently reinterpret newer data.
    """

    payload = dict(value)
    raw_version = payload.get("schema_version")
    if raw_version is None:
        raise ValueError(f"{resource_type} schema_version is required")
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"invalid {resource_type} schema_version: {raw_version!r}"
        ) from exc
    if version == CURRENT_WORKBENCH_SCHEMA_VERSION:
        return payload
    if version > CURRENT_WORKBENCH_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported future {resource_type} schema_version: {version}; "
            f"current={CURRENT_WORKBENCH_SCHEMA_VERSION}"
        )
    raise ValueError(f"unsupported {resource_type} schema_version: {version}")

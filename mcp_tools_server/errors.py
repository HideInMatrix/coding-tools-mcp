"""Error types shared by the protocol and tool layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RpcError(Exception):
    code: int
    message: str
    data: Any = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


@dataclass(slots=True)
class ToolError(Exception):
    code: str
    message: str
    category: str = "runtime"
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "category": self.category,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


def rpc_error_payload(request_id: object, exc: RpcError) -> dict[str, Any]:
    error: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.data is not None:
        error["data"] = exc.data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}

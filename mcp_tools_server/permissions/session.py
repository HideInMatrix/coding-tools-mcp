"""Permission lifecycle for a single MCP Runtime/Profile session."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..errors import RpcError
from ..local_permission_broker import LocalPermissionDecision, redact_for_display
from ..protocol import RequestContext
from .broker import PermissionBroker
from .capabilities import ELICITABLE_PERMISSIONS
from .grants import PermissionGrantStore
from .state import PermissionStateStore, arguments_digest


class PermissionSession:
    def __init__(
        self,
        workspace: Path,
        *,
        broker_client: Any | None = None,
        load_broker_from_env: bool = True,
    ) -> None:
        self.state = PermissionStateStore(workspace)
        self.grants = PermissionGrantStore()
        self.broker = (
            PermissionBroker.from_env()
            if broker_client is None and load_broker_from_env
            else PermissionBroker(broker_client)
        )

    @property
    def broker_client(self) -> Any | None:
        return self.broker.client

    @broker_client.setter
    def broker_client(self, value: Any | None) -> None:
        self.broker.client = value

    @staticmethod
    def arguments_digest(name: str, arguments: dict[str, Any]) -> str:
        return arguments_digest(name, arguments)

    def store_grant(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        permission: str,
        principal: str,
        scope: str,
        ttl_seconds: int,
    ) -> tuple[str, int]:
        return self.grants.store(
            tool_name=tool_name,
            arguments=arguments,
            permission=permission,
            principal=principal,
            scope=scope,
            ttl_seconds=ttl_seconds,
        )

    def stored_permissions_for_call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: RequestContext | None,
    ) -> frozenset[str]:
        if context is None:
            return frozenset()
        return self.grants.permissions_for_call(
            name,
            arguments,
            context.principal,
        )

    def session_permissions_for_call(
        self,
        context: RequestContext | None,
    ) -> frozenset[str]:
        principal = context.principal if context and context.principal else "anonymous"
        return self.grants.session_permissions(principal)

    def grant_session_permissions(self, context: RequestContext | None) -> None:
        principal = context.principal if context and context.principal else "anonymous"
        self.grants.grant_session(principal)

    def permission_round(
        self,
        name: str,
        arguments: dict[str, Any],
        context: RequestContext | None,
    ) -> tuple[frozenset[str], bool]:
        if context is None or context.request_state is None:
            if context and context.input_responses and "permission" in context.input_responses:
                raise RpcError(
                    -32602,
                    "Permission inputResponses require a matching requestState",
                    {"reason": "permission_response_without_state"},
                )
            return frozenset(), False

        state = self.state.verify(
            context.request_state,
            name=name,
            arguments=arguments,
            principal=context.principal,
        )
        responses = context.input_responses or {}
        response = responses.get("permission")
        if not isinstance(response, dict):
            raise RpcError(
                -32602,
                "Permission requestState requires inputResponses.permission",
                {"reason": "permission_response_missing"},
            )
        self.state.consume(
            context.request_state,
            int(state.get("exp", 0)),
        )
        raw_granted = state.get("granted")
        granted = {
            str(item)
            for item in raw_granted
            if isinstance(raw_granted, list) and isinstance(item, str)
        } if isinstance(raw_granted, list) else set()
        action = response.get("action")
        content = response.get("content")
        confirmed = isinstance(content, dict) and content.get("confirm") is True
        if action != "accept" or not confirmed:
            return frozenset(granted), True
        granted.add(str(state["permission"]))
        return frozenset(granted), False

    @staticmethod
    def supports_elicitation(context: RequestContext | None) -> bool:
        if context is None or context.era != "modern":
            return False
        capabilities = context.client_capabilities
        if not isinstance(capabilities, Mapping):
            return False
        elicitation = capabilities.get("elicitation")
        if not isinstance(elicitation, Mapping):
            return False
        if not elicitation:
            return True
        return isinstance(elicitation.get("form"), Mapping)

    @staticmethod
    def permission_message(
        permission: str,
        name: str,
        arguments: dict[str, Any],
        fallback: str,
    ) -> str:
        descriptions = {
            "network": "该操作需要访问网络。",
            "destructive_command": "该操作包含潜在破坏性的 Workspace 命令。",
            "git_metadata_write": "该操作需要写入当前 Workspace 的 .git 元数据。",
            "long_timeout": "该操作需要超过 Safe 模式默认上限的执行时间。",
            "sensitive_env": "该操作需要向子进程传入敏感环境变量。",
            "shell_expansion": "该操作需要启用受限制的 Shell 展开能力。",
            "inline_script": "该操作需要执行内联脚本。",
            "privileged_executable": "沙箱 PATH 中未找到所需工具，需要读取用户工具环境并扩大只读执行范围后重试。",
        }
        try:
            rendered = json.dumps(
                redact_for_display(arguments),
                ensure_ascii=False,
                sort_keys=True,
            )
        except (TypeError, ValueError):
            rendered = str(arguments)
        if len(rendered) > 700:
            rendered = rendered[:697] + "..."
        return (
            f"{descriptions.get(permission, fallback)}\n"
            f"工具：{name}\n"
            f"参数：{rendered}\n"
            "仅授权这一次完全相同的工具调用，是否允许？"
        )

    def input_required(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        permission: str,
        message: str,
        context: RequestContext | None,
        granted: frozenset[str],
    ) -> dict[str, Any] | None:
        if permission not in ELICITABLE_PERMISSIONS:
            return None
        if not self.supports_elicitation(context):
            return None
        assert context is not None
        return {
            "resultType": "input_required",
            "inputRequests": {
                "permission": {
                    "method": "elicitation/create",
                    "params": {
                        "mode": "form",
                        "message": self.permission_message(
                            permission,
                            name,
                            arguments,
                            message,
                        ),
                        "requestedSchema": {
                            "type": "object",
                            "properties": {
                                "confirm": {
                                    "type": "boolean",
                                    "title": "允许本次操作",
                                    "description": "仅授权当前完全相同的工具调用。",
                                    "default": False,
                                }
                            },
                            "required": ["confirm"],
                        },
                    },
                }
            },
            "requestState": self.state.mint(
                name=name,
                arguments=arguments,
                permission=permission,
                principal=context.principal,
                granted=granted,
            ),
        }

    def request_local_permission(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        permission: str,
        message: str,
        context: RequestContext | None,
    ) -> LocalPermissionDecision:
        if permission not in ELICITABLE_PERMISSIONS:
            return LocalPermissionDecision("unavailable")
        return self.broker.request(
            name=name,
            arguments=arguments,
            permission=permission,
            message=message,
            principal=context.principal if context else "anonymous",
        )


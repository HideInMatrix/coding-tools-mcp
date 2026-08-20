from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .schema import validate_workbench_schema


PROMPT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
TEMPLATE_ARGUMENT_PATTERN = re.compile(r"{{\s*([A-Za-z][A-Za-z0-9_-]*)\s*}}")


class ResourceScope(StrEnum):
    BUILTIN = "built-in"
    GLOBAL = "global"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class PromptArgument:
    name: str
    description: str = ""
    required: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PromptArgument":
        name = str(value.get("name") or "").strip()
        if not name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
            raise ValueError(f"invalid prompt argument name: {name!r}")
        return cls(
            name=name,
            description=str(value.get("description") or "").strip(),
            required=bool(value.get("required", False)),
        )

    def mcp_definition(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "required": self.required}
        if self.description:
            payload["description"] = self.description
        return payload


@dataclass(frozen=True, slots=True)
class PromptMessage:
    role: str
    content: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PromptMessage":
        role = str(value.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            raise ValueError("prompt message role must be user or assistant")
        content = value.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("prompt message content must be a non-empty string")
        return cls(role=role, content=content)

    def render(self, arguments: Mapping[str, str]) -> dict[str, Any]:
        def replace(match: re.Match[str]) -> str:
            return arguments.get(match.group(1), "")

        return {
            "role": self.role,
            "content": {
                "type": "text",
                "text": TEMPLATE_ARGUMENT_PATTERN.sub(replace, self.content),
            },
        }


@dataclass(frozen=True, slots=True)
class PromptDefinition:
    id: str
    name: str
    description: str
    arguments: tuple[PromptArgument, ...]
    messages: tuple[PromptMessage, ...]
    version: int = 1
    schema_version: int = 1
    scope: ResourceScope = ResourceScope.BUILTIN
    source: str = "built-in"

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        scope: ResourceScope = ResourceScope.BUILTIN,
        source: str,
    ) -> "PromptDefinition":
        value = validate_workbench_schema(value, resource_type="prompt")
        schema_version = int(value.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(f"unsupported prompt schema_version: {schema_version}")
        prompt_id = str(value.get("id") or "").strip()
        if not PROMPT_ID_PATTERN.fullmatch(prompt_id):
            raise ValueError(f"invalid prompt id: {prompt_id!r}")
        name = str(value.get("name") or prompt_id).strip()
        if not name:
            raise ValueError("prompt name must not be empty")
        description = str(value.get("description") or "").strip()
        version = int(value.get("version", 1))
        if version < 1:
            raise ValueError("prompt version must be >= 1")

        raw_arguments = value.get("arguments", [])
        if not isinstance(raw_arguments, list):
            raise ValueError("prompt arguments must be a list")
        arguments = tuple(
            PromptArgument.from_mapping(item)
            for item in raw_arguments
            if isinstance(item, Mapping)
        )
        if len(arguments) != len(raw_arguments):
            raise ValueError("prompt arguments must contain objects only")
        argument_names = [item.name for item in arguments]
        if len(set(argument_names)) != len(argument_names):
            raise ValueError("prompt argument names must be unique")

        raw_messages = value.get("messages", [])
        if not isinstance(raw_messages, list) or not raw_messages:
            raise ValueError("prompt messages must be a non-empty list")
        messages = tuple(
            PromptMessage.from_mapping(item)
            for item in raw_messages
            if isinstance(item, Mapping)
        )
        if len(messages) != len(raw_messages):
            raise ValueError("prompt messages must contain objects only")

        known_arguments = set(argument_names)
        for message in messages:
            referenced = set(TEMPLATE_ARGUMENT_PATTERN.findall(message.content))
            unknown = referenced - known_arguments
            if unknown:
                raise ValueError(
                    f"prompt {prompt_id} references unknown arguments: {sorted(unknown)}"
                )

        return cls(
            id=prompt_id,
            name=name,
            description=description,
            arguments=arguments,
            messages=messages,
            version=version,
            schema_version=schema_version,
            scope=scope,
            source=source,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "scope": self.scope.value,
            "arguments": [item.mcp_definition() for item in self.arguments],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "arguments": [item.mcp_definition() for item in self.arguments],
            "messages": [
                {"role": item.role, "content": item.content}
                for item in self.messages
            ],
        }

    def mcp_definition(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.id, "title": self.name}
        if self.description:
            payload["description"] = self.description
        if self.arguments:
            payload["arguments"] = [item.mcp_definition() for item in self.arguments]
        return payload

    def render(self, raw_arguments: Mapping[str, Any] | None) -> dict[str, Any]:
        values = raw_arguments or {}
        if not isinstance(values, Mapping):
            raise ValueError("prompt arguments must be an object")
        expected = {item.name: item for item in self.arguments}
        unknown = set(values) - set(expected)
        if unknown:
            raise ValueError(f"unknown prompt arguments: {sorted(unknown)}")

        rendered: dict[str, str] = {}
        for name, argument in expected.items():
            raw = values.get(name)
            if raw is None:
                if argument.required:
                    raise ValueError(f"missing required prompt argument: {name}")
                rendered[name] = ""
                continue
            if not isinstance(raw, str):
                raise ValueError(f"prompt argument must be a string: {name}")
            rendered[name] = raw

        payload: dict[str, Any] = {
            "messages": [message.render(rendered) for message in self.messages],
        }
        if self.description:
            payload["description"] = self.description
        return payload


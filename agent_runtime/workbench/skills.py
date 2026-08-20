from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .models import ResourceScope, WORKBENCH_ID_PATTERN
from .schema import validate_workbench_schema

SKILL_SCOPE_PRECEDENCE = (
    ResourceScope.GLOBAL,
)


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    id: str
    name: str
    description: str
    artifacts: tuple[str, ...]
    method_document: str
    version: int = 1
    schema_version: int = 1
    scope: ResourceScope = ResourceScope.BUILTIN
    source: str = "built-in"

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        method_document: str,
        scope: ResourceScope,
        source: str,
    ) -> "SkillDefinition":
        value = validate_workbench_schema(value, resource_type="skill")
        schema_version = int(value.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(f"unsupported skill schema_version: {schema_version}")

        skill_id = str(value.get("id") or "").strip()
        if not WORKBENCH_ID_PATTERN.fullmatch(skill_id):
            raise ValueError(f"invalid skill id: {skill_id!r}")
        name = str(value.get("name") or skill_id).strip()
        if not name:
            raise ValueError("skill name must not be empty")
        description = str(value.get("description") or "").strip()
        version = int(value.get("version", 1))
        if version < 1:
            raise ValueError("skill version must be >= 1")

        raw_artifacts = value.get("artifacts", [])
        if not isinstance(raw_artifacts, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_artifacts
        ):
            raise ValueError("skill artifacts must be a list of non-empty strings")
        artifacts = tuple(dict.fromkeys(item.strip() for item in raw_artifacts))
        if len(artifacts) != len(raw_artifacts):
            raise ValueError("skill artifacts must be unique")
        for artifact in artifacts:
            path = PurePosixPath(artifact.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"skill artifact must be relative: {artifact}")

        method = method_document.strip()
        if not method:
            raise ValueError("SKILL.md must not be empty")

        return cls(
            id=skill_id,
            name=name,
            description=description,
            artifacts=artifacts,
            method_document=method,
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
            "artifacts": list(self.artifacts),
        }

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "artifacts": list(self.artifacts),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "schema_version": self.schema_version,
            "source": self.source,
            "method_document": self.method_document,
        }


class SkillRegistry:
    def __init__(self, definitions: Iterable[SkillDefinition] = ()) -> None:
        self._layers: dict[ResourceScope, dict[str, SkillDefinition]] = {
            scope: {} for scope in ResourceScope
        }
        for definition in definitions:
            self.register(definition, replace=True)

    def register(self, definition: SkillDefinition, *, replace: bool = False) -> None:
        layer = self._layers[definition.scope]
        if not replace and definition.id in layer:
            raise ValueError(f"duplicate skill registration: {definition.id}")
        layer[definition.id] = definition

    def remove(self, skill_id: str, *, scope: ResourceScope) -> bool:
        return self._layers[scope].pop(skill_id, None) is not None

    def replace_scope(
        self,
        scope: ResourceScope,
        definitions: Iterable[SkillDefinition],
    ) -> None:
        self._layers[scope] = {item.id: item for item in definitions}

    def replace_with(self, other: "SkillRegistry") -> None:
        for scope in ResourceScope:
            self._layers[scope] = dict(other._layers[scope])

    def get(self, skill_id: str) -> SkillDefinition | None:
        for scope in SKILL_SCOPE_PRECEDENCE:
            definition = self._layers[scope].get(skill_id)
            if definition is not None:
                return definition
        return None

    def list(self) -> tuple[SkillDefinition, ...]:
        ids = set().union(*(layer.keys() for layer in self._layers.values()))
        definitions = [self.get(skill_id) for skill_id in ids]
        return tuple(
            sorted(
                (item for item in definitions if item is not None),
                key=lambda item: item.id,
            )
        )


def build_skill_registry(
    *,
    global_skill_roots: Iterable[Path] = (),
) -> SkillRegistry:
    from .skill_store import SkillStore

    registry = SkillRegistry()

    for root in global_skill_roots:
        store = SkillStore(
            directory=root,
            scope=ResourceScope.GLOBAL,
            source_prefix="global",
        )
        for definition in store.list():
            registry.register(definition, replace=True)
    return registry

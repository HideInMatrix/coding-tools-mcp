from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .models import PromptDefinition, ResourceScope
from .global_assets import global_asset_root
from .prompt_store import PromptStore
from .prompts import PromptRegistry, build_prompt_registry
from .skill_store import SkillStore
from .skills import (
    SkillDefinition,
    SkillRegistry,
    _validate_skill_references,
    build_skill_registry,
)


class CapabilityAssetService:
    """Shared Prompt/Skill domain service for Desktop and MCP authoring.

    Phase 10 deliberately places persistence, validation, registry refresh and
    optimistic concurrency behind one service so later GUI and MCP authoring
    surfaces cannot grow separate save semantics.
    """

    def __init__(
        self,
        *,
        known_tool_names: Iterable[str],
        known_mcp_tool_keys: Iterable[str] = (),
        global_root: Path | None = None,
    ) -> None:
        self.global_root = global_asset_root(global_root)
        self.known_tool_names = frozenset(str(item) for item in known_tool_names)
        self.known_mcp_tool_keys = frozenset(str(item) for item in known_mcp_tool_keys)
        self.prompt_store = PromptStore(
            directory=self.global_root / "prompts",
            scope=ResourceScope.GLOBAL,
            source_prefix="global",
        )
        self.skill_store = SkillStore(
            directory=self.global_root / "skills",
            scope=ResourceScope.GLOBAL,
            source_prefix="global",
        )
        self.prompt_registry = PromptRegistry()
        self.skill_registry = SkillRegistry()
        self.refresh()

    def refresh_prompts(self) -> PromptRegistry:
        loaded = build_prompt_registry(
            global_prompt_roots=(self.global_root / "prompts",),
        )
        self.prompt_registry.replace_with(loaded)
        return self.prompt_registry

    def refresh_skills(self) -> SkillRegistry:
        loaded = build_skill_registry(
            prompt_registry=self.prompt_registry,
            known_tool_names=self.known_tool_names,
            known_mcp_tool_keys=self.known_mcp_tool_keys,
            global_skill_roots=(self.global_root / "skills",),
        )
        self.skill_registry.replace_with(loaded)
        return self.skill_registry

    def refresh(self) -> None:
        self.refresh_prompts()
        self.refresh_skills()

    def validate_prompt(self, raw: Mapping[str, Any]) -> PromptDefinition:
        return PromptDefinition.from_mapping(
            raw,
            scope=ResourceScope.GLOBAL,
            source="global:draft",
        )

    def save_prompt(
        self,
        raw: Mapping[str, Any],
        *,
        expected_version: int,
    ) -> PromptDefinition:
        definition = self.validate_prompt(raw)
        saved = self.prompt_store.save(
            definition,
            expected_version=expected_version,
        )
        # Prompt changes can affect Skill entry_prompt references, so both
        # registries are refreshed as one domain operation.
        self.refresh()
        return saved

    def delete_prompt(self, prompt_id: str) -> bool:
        current = self.prompt_store.get(prompt_id)
        if current is None:
            return False

        fallback = self.prompt_registry.get_without_scope(
            prompt_id,
            ResourceScope.GLOBAL,
        )
        if fallback is None:
            dependants = [
                item.id
                for item in self.skill_registry.list()
                if item.entry_prompt == prompt_id
            ]
            if dependants:
                raise ValueError(
                    f"Prompt {prompt_id} is referenced by Skills: {sorted(dependants)}"
                )

        deleted = self.prompt_store.delete(prompt_id)
        if deleted:
            self.refresh()
        return deleted

    def validate_skill(self, raw: Mapping[str, Any]) -> SkillDefinition:
        method_document = str(raw.get("method_document") or "")
        definition = SkillDefinition.from_mapping(
            raw,
            method_document=method_document,
            scope=ResourceScope.GLOBAL,
            source="global:draft",
        )
        _validate_skill_references(
            definition,
            prompt_registry=self.prompt_registry,
            known_tool_names=self.known_tool_names,
            known_mcp_tool_keys=self.known_mcp_tool_keys,
        )
        return definition

    def set_known_mcp_tool_keys(self, values: Iterable[str]) -> None:
        next_values = frozenset(str(item) for item in values)
        if next_values == self.known_mcp_tool_keys:
            return
        self.known_mcp_tool_keys = next_values
        self.refresh_skills()

    def save_skill(
        self,
        raw: Mapping[str, Any],
        *,
        expected_version: int,
    ) -> SkillDefinition:
        definition = self.validate_skill(raw)
        saved = self.skill_store.save(
            definition,
            expected_version=expected_version,
        )
        self.refresh_skills()
        return saved

    def delete_skill(self, skill_id: str) -> bool:
        deleted = self.skill_store.delete(skill_id)
        if deleted:
            self.refresh_skills()
        return deleted


from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .global_assets import global_asset_root
from .models import ResourceScope
from .skill_store import SkillStore
from .skills import SkillDefinition, SkillRegistry, build_skill_registry


class CapabilityAssetService:
    """Global Skill persistence, validation, and registry refresh."""

    def __init__(self, *, global_root: Path | None = None) -> None:
        self.global_root = global_asset_root(global_root)
        self.skill_store = SkillStore(
            directory=self.global_root / "skills",
            scope=ResourceScope.GLOBAL,
            source_prefix="global",
        )
        self.skill_registry = SkillRegistry()
        self.refresh_skills()

    def refresh_skills(self) -> SkillRegistry:
        loaded = build_skill_registry(
            global_skill_roots=(self.global_root / "skills",),
        )
        self.skill_registry.replace_with(loaded)
        return self.skill_registry

    def refresh(self) -> None:
        self.refresh_skills()

    def validate_skill(self, raw: Mapping[str, Any]) -> SkillDefinition:
        method_document = str(raw.get("method_document") or "")
        return SkillDefinition.from_mapping(
            raw,
            method_document=method_document,
            scope=ResourceScope.GLOBAL,
            source="global:draft",
        )

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

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .models import PROMPT_ID_PATTERN, ResourceScope
from .schema import validate_workbench_schema
from .prompts import PromptRegistry
from .tool_references import ToolReference

SKILL_SCOPE_PRECEDENCE = (
    ResourceScope.GLOBAL,
    ResourceScope.BUILTIN,
)


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    id: str
    name: str
    description: str
    tool_references: tuple[ToolReference, ...]
    entry_prompt: str | None
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
        if not PROMPT_ID_PATTERN.fullmatch(skill_id):
            raise ValueError(f"invalid skill id: {skill_id!r}")
        name = str(value.get("name") or skill_id).strip()
        if not name:
            raise ValueError("skill name must not be empty")
        description = str(value.get("description") or "").strip()
        version = int(value.get("version", 1))
        if version < 1:
            raise ValueError("skill version must be >= 1")

        raw_tools = value.get("tool_references", [])
        if not isinstance(raw_tools, list):
            raise ValueError("skill tool_references must be a list")
        tool_references: list[ToolReference] = []
        for item in raw_tools:
            if not isinstance(item, Mapping):
                raise ValueError("skill tool_references must contain Tool Reference objects")
            tool_references.append(ToolReference.from_value(item))
        reference_keys = [item.key for item in tool_references]
        if len(set(reference_keys)) != len(reference_keys):
            raise ValueError("skill tool_references must be unique")

        raw_prompt = value.get("entry_prompt")
        entry_prompt = str(raw_prompt).strip() if raw_prompt is not None else None
        if entry_prompt == "":
            entry_prompt = None
        if entry_prompt is not None and not PROMPT_ID_PATTERN.fullmatch(entry_prompt):
            raise ValueError(f"invalid skill entry_prompt: {entry_prompt!r}")

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
            tool_references=tuple(tool_references),
            entry_prompt=entry_prompt,
            artifacts=artifacts,
            method_document=method,
            version=version,
            schema_version=schema_version,
            scope=scope,
            source=source,
        )

    def effective_tools(self, available_tools: Set[str] | set[str] | frozenset[str]) -> tuple[str, ...]:
        """Return the Skill allowlist intersected with the Runtime tool set."""

        return tuple(
            reference.tool_name
            for reference in self.tool_references
            if reference.provider == "system" and reference.tool_name in available_tools
        )

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "scope": self.scope.value,
            "entry_prompt": self.entry_prompt,
            "tool_references": [item.to_dict() for item in self.tool_references],
            "artifacts": list(self.artifacts),
        }

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "entry_prompt": self.entry_prompt,
            "tool_references": [item.to_dict() for item in self.tool_references],
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


def _system_tool_references(*names: str) -> list[dict[str, str]]:
    return [{"provider": "system", "tool_name": name} for name in names]


BUILTIN_SKILLS: tuple[tuple[dict[str, Any], str], ...] = (
    (
        {
            "schema_version": 1,
            "id": "project-analysis",
            "name": "项目分析",
            "description": "建立 Workspace 的技术栈、入口、模块、依赖和测试地图。",
            "entry_prompt": "project-analysis",
            "tool_references": _system_tool_references("server_info", "list_dir", "list_files", "read_file", "search_text"),
            "artifacts": ["project-map.md"],
        },
        """# Project Analysis\n\n1. 先读取项目入口、依赖清单与目录结构。\n2. 使用 search 定位核心模块和测试。\n3. 区分事实、推断和待验证项。\n4. 输出项目地图以及下一步建议。\n5. 本 Skill 不负责直接修改代码。""",
    ),
    (
        {
            "schema_version": 1,
            "id": "bug-investigation",
            "name": "Bug 调查",
            "description": "按复现、证据、根因、最小修复和回归的顺序调查问题。",
            "entry_prompt": "bug-investigation",
            "tool_references": _system_tool_references("read_file", "search_text", "list_files", "exec_process", "git_diff"),
            "artifacts": ["bug-investigation.md", "test-report.md"],
        },
        """# Bug Investigation\n\n1. 明确症状与可复现条件。\n2. 收集日志、代码路径和失败测试。\n3. 建立从现象到根因的证据链。\n4. 修改前先确定最小修复范围。\n5. 修复后执行针对性回归并记录结果。""",
    ),
    (
        {
            "schema_version": 1,
            "id": "reverse-engineering",
            "name": "旧项目逆向工程",
            "description": "建立架构、接口、数据、行为和测试基线。",
            "entry_prompt": "reverse-engineering",
            "tool_references": _system_tool_references("list_dir", "list_files", "read_file", "search_text", "exec_process"),
            "artifacts": ["architecture.md", "api-map.md", "behavior-map.md", "gap-analysis.md"],
        },
        """# Reverse Engineering\n\n1. Repository Recon：确认入口、技术栈、模块边界。\n2. Architecture Mapping：建立模块与调用关系。\n3. API/Data Mapping：记录接口、数据模型与状态转换。\n4. Behavior Extraction：从实现和运行结果提取真实行为。\n5. Baseline Tests：为关键行为建立可重复验证。\n6. Gap Analysis：对目标实现建立差异矩阵。""",
    ),
    (
        {
            "schema_version": 1,
            "id": "code-review",
            "name": "代码审查",
            "description": "检查正确性、回归风险、安全边界和测试缺口。",
            "entry_prompt": "code-review",
            "tool_references": _system_tool_references("read_file", "search_text", "git_diff", "git_show"),
            "artifacts": ["code-review.md"],
        },
        """# Code Review\n\n1. 先确定变更范围与调用影响面。\n2. 优先检查正确性和安全问题。\n3. 检查错误处理、边界条件和兼容性。\n4. 检查测试是否覆盖真实回归风险。\n5. Findings 按严重程度排序，并给出可验证依据。""",
    ),
    (
        {
            "schema_version": 1,
            "id": "spec-development",
            "name": "Spec 驱动开发",
            "description": "先固化需求与设计，再拆任务、实现、测试和验收。",
            "entry_prompt": "spec-development",
            "tool_references": _system_tool_references("list_files", "read_file", "search_text", "apply_patch"),
            "artifacts": ["requirements.md", "design.md", "tasks.md", "acceptance.md"],
        },
        """# Spec Development\n\n1. Requirements：明确用户目标、范围、非目标和验收条件。\n2. Design：先建立领域模型、数据流、接口和权限边界。\n3. Tasks：把设计拆成有依赖顺序的可验证任务。\n4. Implementation：只按已确认设计实施。\n5. Test & Acceptance：执行测试并逐条对应验收条件。""",
    ),
    (
        {
            "schema_version": 1,
            "id": "release-validation",
            "name": "Release Validation",
            "description": "发布前检查版本、构建、测试、产物和自动更新契约。",
            "entry_prompt": "release-validation",
            "tool_references": _system_tool_references("server_info", "read_file", "search_text", "exec_process", "git_diff"),
            "artifacts": ["release-validation.md"],
        },
        """# Release Validation\n\n1. 确认版本号和发布资产命名来源。\n2. 执行能够在当前环境中实际运行的构建与测试。\n3. 检查平台特定产物和更新器选择逻辑。\n4. 检查 SHA-256、兼容迁移包和回滚路径。\n5. 把未验证项明确标为未验证，不得推断为通过。""",
    ),
)


def _validate_skill_references(
    definition: SkillDefinition,
    *,
    prompt_registry: PromptRegistry,
    known_tool_names: frozenset[str],
    known_mcp_tool_keys: frozenset[str] = frozenset(),
) -> None:
    if definition.entry_prompt and prompt_registry.get(definition.entry_prompt) is None:
        raise ValueError(
            f"skill {definition.id} references unknown prompt: {definition.entry_prompt}"
        )
    unknown_tools = {
        item.tool_name
        for item in definition.tool_references
        if item.provider == "system" and item.tool_name not in known_tool_names
    }
    if unknown_tools:
        raise ValueError(
            f"skill {definition.id} references unknown tools: {sorted(unknown_tools)}"
        )
    unknown_mcp_tools = {
        item.key
        for item in definition.tool_references
        if item.provider == "mcp" and item.key not in known_mcp_tool_keys
    }
    if unknown_mcp_tools:
        raise ValueError(
            f"skill {definition.id} references unavailable MCP tools: {sorted(unknown_mcp_tools)}"
        )


def build_skill_registry(
    *,
    prompt_registry: PromptRegistry,
    known_tool_names: Iterable[str],
    known_mcp_tool_keys: Iterable[str] = (),
    global_skill_roots: Iterable[Path] = (),
) -> SkillRegistry:
    from .skill_store import SkillStore

    tools = frozenset(known_tool_names)
    mcp_tools = frozenset(known_mcp_tool_keys)
    registry = SkillRegistry()

    for raw, method in BUILTIN_SKILLS:
        definition = SkillDefinition.from_mapping(
            raw,
            method_document=method,
            scope=ResourceScope.BUILTIN,
            source="built-in",
        )
        _validate_skill_references(
            definition,
            prompt_registry=prompt_registry,
            known_tool_names=tools,
            known_mcp_tool_keys=mcp_tools,
        )
        registry.register(definition, replace=True)

    for root in global_skill_roots:
        store = SkillStore(
            directory=root,
            scope=ResourceScope.GLOBAL,
            source_prefix="global",
        )
        for definition in store.list():
            _validate_skill_references(
                definition,
                prompt_registry=prompt_registry,
                known_tool_names=tools,
                known_mcp_tool_keys=mcp_tools,
            )
            registry.register(definition, replace=True)
    return registry


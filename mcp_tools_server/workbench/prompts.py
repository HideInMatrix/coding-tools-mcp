from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import PromptDefinition, ResourceScope
from .prompt_store import PromptStore

PROMPT_SCOPE_PRECEDENCE = (
    ResourceScope.GLOBAL,
    ResourceScope.BUILTIN,
)


BUILTIN_PROMPTS: tuple[dict[str, Any], ...] = (
    {
        "schema_version": 1,
        "id": "project-analysis",
        "name": "项目分析",
        "description": "先建立项目结构、入口、依赖与关键模块地图，再给出下一步建议。",
        "arguments": [
            {"name": "goal", "description": "本次分析希望解决的问题", "required": False}
        ],
        "messages": [
            {
                "role": "user",
                "content": "分析当前 Workspace。先建立目录、技术栈、入口、关键模块和测试地图，优先 read/search 后再下结论。目标：{{goal}}",
            }
        ],
    },
    {
        "schema_version": 1,
        "id": "bug-investigation",
        "name": "Bug 调查",
        "description": "以证据链为中心进行复现、定位、修复和回归。",
        "arguments": [
            {"name": "symptom", "description": "Bug 现象", "required": True}
        ],
        "messages": [
            {
                "role": "user",
                "content": "调查 Bug：{{symptom}}。先复现或收集证据，再定位根因；不要用猜测替代日志、代码路径和测试。修复后给出回归点。",
            }
        ],
    },
    {
        "schema_version": 1,
        "id": "reverse-engineering",
        "name": "旧项目逆向工程",
        "description": "建立旧项目的架构、接口、数据、行为和测试基线。",
        "arguments": [
            {"name": "target", "description": "需要逆向分析的模块或目标", "required": False}
        ],
        "messages": [
            {
                "role": "user",
                "content": "对当前项目执行逆向工程分析，目标：{{target}}。按 Repository Recon、Architecture Mapping、API/Data Mapping、Behavior Extraction、Baseline Tests、Gap Analysis 顺序推进。",
            }
        ],
    },
    {
        "schema_version": 1,
        "id": "code-review",
        "name": "代码审查",
        "description": "检查正确性、回归风险、安全边界和可维护性。",
        "arguments": [
            {"name": "target", "description": "审查范围", "required": True}
        ],
        "messages": [
            {
                "role": "user",
                "content": "审查 {{target}}。优先发现真实缺陷、回归风险、安全问题和测试缺口，按严重程度排序，并给出可验证的修改建议。",
            }
        ],
    },
    {
        "schema_version": 1,
        "id": "spec-development",
        "name": "Spec 驱动开发",
        "description": "按需求、设计、任务、实现、测试和验收推进功能开发。",
        "arguments": [
            {"name": "feature", "description": "要实现的功能", "required": True}
        ],
        "messages": [
            {
                "role": "user",
                "content": "以 Spec 驱动方式实现：{{feature}}。先形成 requirements，再形成 design，然后拆 tasks；关键设计经确认后再实现，最后执行测试与验收。",
            }
        ],
    },
    {
        "schema_version": 1,
        "id": "workflow-authoring",
        "name": "Workflow Authoring",
        "description": "把用户自然语言转换成可验证的 AI Workbench Workflow Definition。",
        "arguments": [
            {"name": "request", "description": "用户希望创建或修改的工作流", "required": True}
        ],
        "messages": [
            {
                "role": "user",
                "content": "根据以下需求创建或修改 AI Workbench Workflow：{{request}}。先调用 workflow_authoring_context 获取当前 Prompt/Skill/Tool 与 Schema；修改已有流程时先 workflow_get。生成后必须 workflow_validate；只有验证通过才能 workflow_save，并携带正确 expected_version。不要保存 Password/Token/Secret/API Key 明文。",
            }
        ],
    },
    {
        "schema_version": 1,
        "id": "release-validation",
        "name": "Release Validation",
        "description": "在发布前验证版本、构建、测试、产物和更新契约。",
        "arguments": [
            {"name": "target", "description": "本次发布目标或版本", "required": False}
        ],
        "messages": [
            {
                "role": "user",
                "content": "验证发布目标：{{target}}。检查版本来源、前后端构建、关键测试、平台产物命名、更新包与 SHA-256 契约、已知环境限制。不要把未执行的检查写成已通过。输出可审计的 release validation report。",
            }
        ],
    },
)


class PromptRegistry:
    def __init__(self, definitions: Iterable[PromptDefinition] = ()) -> None:
        self._layers: dict[ResourceScope, dict[str, PromptDefinition]] = {
            scope: {} for scope in ResourceScope
        }
        for definition in definitions:
            self.register(definition, replace=True)

    def register(self, definition: PromptDefinition, *, replace: bool = False) -> None:
        layer = self._layers[definition.scope]
        if not replace and definition.id in layer:
            raise ValueError(f"duplicate prompt registration: {definition.id}")
        layer[definition.id] = definition

    def remove(self, prompt_id: str, *, scope: ResourceScope) -> bool:
        return self._layers[scope].pop(prompt_id, None) is not None

    def replace_scope(
        self,
        scope: ResourceScope,
        definitions: Iterable[PromptDefinition],
    ) -> None:
        self._layers[scope] = {item.id: item for item in definitions}

    def replace_with(self, other: "PromptRegistry") -> None:
        for scope in ResourceScope:
            self._layers[scope] = dict(other._layers[scope])

    def get(self, prompt_id: str) -> PromptDefinition | None:
        for scope in PROMPT_SCOPE_PRECEDENCE:
            definition = self._layers[scope].get(prompt_id)
            if definition is not None:
                return definition
        return None

    def get_in_scope(
        self,
        prompt_id: str,
        scope: ResourceScope,
    ) -> PromptDefinition | None:
        return self._layers[scope].get(prompt_id)

    def get_without_scope(
        self,
        prompt_id: str,
        excluded_scope: ResourceScope,
    ) -> PromptDefinition | None:
        for scope in PROMPT_SCOPE_PRECEDENCE:
            if scope == excluded_scope:
                continue
            definition = self._layers[scope].get(prompt_id)
            if definition is not None:
                return definition
        return None

    def list(self) -> tuple[PromptDefinition, ...]:
        ids = set().union(*(layer.keys() for layer in self._layers.values()))
        definitions = [self.get(prompt_id) for prompt_id in ids]
        return tuple(
            sorted(
                (item for item in definitions if item is not None),
                key=lambda item: item.id,
            )
        )

    def mcp_list(self) -> dict[str, Any]:
        return {"prompts": [definition.mcp_definition() for definition in self.list()]}

    def render(self, prompt_id: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        definition = self.get(prompt_id)
        if definition is None:
            raise KeyError(prompt_id)
        return definition.render(arguments)


def build_prompt_registry(
    *,
    global_prompt_roots: Iterable[Path] = (),
) -> PromptRegistry:
    registry = PromptRegistry(
        PromptDefinition.from_mapping(
            item,
            scope=ResourceScope.BUILTIN,
            source="built-in",
        )
        for item in BUILTIN_PROMPTS
    )
    for root in global_prompt_roots:
        store = PromptStore(
            directory=root,
            scope=ResourceScope.GLOBAL,
            source_prefix="global",
        )
        for definition in store.list():
            registry.register(definition, replace=True)
    return registry


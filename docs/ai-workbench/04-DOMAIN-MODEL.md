# AI Workbench 领域模型

## 1. 目的

本文固定 AI Workbench 的核心领域对象、依赖关系和生命周期。后续 Python、MCP、DesktopAPI 和 Vue Flow 都必须围绕同一领域模型实现，不能各自定义第二套语义。

---

## 2. 核心对象

```text
Tool
Tool Reference
MCP Connection
Prompt
Skill
Workflow Definition
Workflow Run
Artifact
Approval
```

System Tool 由现有 MCP Tool Framework 提供；外部 MCP Tool 由用户管理的 MCP Connection Discovery 提供。Workflow 和 Skill 都通过稳定 Tool Reference 引用能力，不复制 Tool 实现。

---

## 3. Tool

Tool 是最小可执行能力，例如：

```text
read_file
search_text
apply_patch
exec_process
git_diff
```

Tool Provider：

```text
system  MicroMatrix Workbench 自身实现
mcp     外部 MCP Connection discover 出来的 Tool
```

稳定引用：

```text
ToolReference
├── provider
├── tool_name
└── connection_id?   # provider=mcp 时必填

system:read_file
mcp:github:create_issue
```

约束：

- 保持原子化；
- System Tool 由 ToolRegistry/Dispatcher 管理；
- MCP Tool 由 MCP Connection Store / Discovery / Effective Tool Catalog 管理；
- 权限由现有 Capability、PermissionSession、PermissionBroker、Sandbox 决定；
- Workbench 不复制 Tool 实现。

外部 MCP Tool 从 Workflow 执行时仍经过 `Runtime.call_tool(mcp_connection_call_tool)`，因此不会因为 Provider 不同而绕过本地权限边界。

---

## 4. Prompt

Prompt 是可参数化的模型输入模板，不直接执行 Tool。

```text
PromptDefinition
├── id
├── name
├── description
├── arguments[]
├── messages[]
├── scope
└── schema_version
```

生命周期：

```text
load global asset -> validate -> registry merge -> render
```

作用域优先级：

```text
Global > Built-in
```

用户或 AI 新建、编辑 Prompt 都写入应用级 Global Store；Prompt 不属于 Workspace。

MCP 映射：

```text
prompts/list
prompts/get
```

---

## 5. Skill

Skill 是“完成某类工程任务的方法包”，不是单条 Prompt。

```text
SkillDefinition
├── id
├── name
├── description
├── entry_prompt?
├── tool_references[]
├── artifacts[]
├── scope
└── method_document -> SKILL.md
```

目录：

```text
Application Data / Workbench / skills / <skill-id>/
├── skill.json
├── SKILL.md
├── templates/
└── references/
```

新建或编辑 Skill 写入 Global Store；Skill 不存在 Workspace 作用域，管理页面也不以 Workspace Target 作为 Skill 的归属条件。

Skill 可以：

- 引用 Prompt；
- 定义方法论；
- 定义建议/允许使用的 Tool；
- 定义预期 Artifact。

Skill 不可以：

- 提升 Runtime 权限；
- 绕过 Permission Broker；
- 直接保存 secret；
- 运行任意未声明脚本。

---

## 6. Workflow Definition

Workflow Definition 是工作流唯一权威定义。

```text
WorkflowDefinition
├── id
├── name
├── description
├── version
├── entry_node_id
├── inputs_schema
├── tags[]
├── nodes[]
├── edges[]
├── metadata
└── scope
```

`description / inputs_schema / tags` 是 AI discovery contract，不属于 Vue Flow 坐标数据。`workflow_list` 直接返回这些字段；`workflow_start.inputs` 在创建 Run 前按 `inputs_schema` 校验。

`inputs_schema` 未声明时使用开放 object schema。`description` 是 Workflow Definition 的必填领域字段，Authoring 与 Runtime 使用同一合法性标准。

Node 第一版支持：

```text
prompt
skill
tool
approval
condition
artifact
```

Vue Flow 的 `position` 可以保存到 Definition 用于布局，但 Engine 不得依赖坐标。

Workflow Definition 本身没有“当前执行到哪里”的状态。

---

## 7. Workflow Run

Run 是某个 Workflow Definition Version 的一次真实执行实例。

```text
WorkflowRun
├── run_id
├── workflow_id
├── workflow_version
├── workspace
├── status
├── current_node_ids[]
├── node_states{}
├── artifacts[]
├── approvals[]
├── errors[]
├── started_at
└── updated_at
```

状态建议：

```text
pending
running
waiting_model
waiting_approval
succeeded
failed
cancelled
```

Definition 更新后，已存在的 Run 继续绑定创建时的 workflow_version，不能悄悄切到新版本。

Run 同时保存创建时的完整 `workflow_snapshot`，用于跨版本恢复。

---

## 8. Artifact

Artifact 是 Workflow/Skill 产生的可复用中间成果，例如：

```text
requirements.md
architecture.md
api-map.json
test-report.json
gap-analysis.md
```

Run 中只保存稳定引用和元数据，不强制把大型内容全部内嵌到 Run JSON。

```text
ArtifactRef
├── artifact_id
├── type
├── path/resource_uri
├── producer_node_id
├── created_at
└── metadata
```

第一版 Artifact Node 只允许把已经完成节点的 output 写入当前 Run 自己的 artifact 目录。不得通过 Workflow 配置指定绝对路径或 `..` 跳出 Run 目录。

---

## 9. Approval

Approval 是显式的人机边界，而不是普通 Prompt。

```text
ApprovalRequest
├── approval_id
├── run_id
├── node_id
├── title
├── description
├── status
├── requested_at
└── resolved_at
```

Workflow Engine 到达 approval node 后必须暂停对应 Run，直到用户明确批准/拒绝。

Approval 不能替代 Tool Permission；两者语义不同：

```text
Approval = 是否继续业务流程
Permission = 是否允许执行某个受限操作
```

AI 的 `workflow_continue` 不能解决 Approval。Approval Resolution 属于 Desktop/UI 用户通道。

---

## 10. 对象关系

```text
Prompt <──── Skill
              ▲
              │
Tool <────────┤
              │
              ▼
      Workflow Definition
              │
              │ start
              ▼
        Workflow Run
          │       │
          ▼       ▼
      Artifact  Approval
```

Workflow Node 只能引用 Registry 中存在的 Prompt / Skill / Tool。

---

## 11. AI 与 Vue Flow 的统一写入模型

```text
用户拖拽 Vue Flow
        │
        ▼
Workflow Draft
        │
        ├──────────────┐
        │              │
        ▼              ▼
workflow_validate   AI workflow_validate
        │              │
        └──────┬───────┘
               ▼
          workflow_save
               │
               ▼
        Workflow Registry
```

AI 不直接操作 Vue Flow 节点组件；Vue Flow 也不直接绕过后端 Validator 保存。

---

## 12. 权限模型

执行有效权限是交集，而不是叠加：

```text
Skill tool_references
        ∩
Workflow node declaration
        ∩
Runtime Capability
        ∩
Permission grants
        ∩
Sandbox policy
```

任何 Workbench 对象都不能扩大 Runtime 本身没有的能力。

---

## 13. 版本与兼容

所有持久化资源必须至少包含：

```text
schema_version
```

Workflow 另外包含业务版本：

```text
version
```

区别：

- `schema_version`：文件结构版本；
- `version`：用户工作流定义版本。

未来迁移只允许通过显式 migration 处理，禁止 loader 静默改变业务含义。

---

## 14. Phase 依赖

```text
Prompt
  ↓
Skill
  ↓
Workflow Definition
  ↓
Engine
  ↓
Run / Artifact / Approval
  ↓
Vue Flow
  ↓
AI Authoring
```

此依赖关系是后续计划执行的门禁依据。

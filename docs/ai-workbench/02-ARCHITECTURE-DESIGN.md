# AI Workbench 设计方案

## 1. 总体架构

```text
                              AI Client
                                  │
                              MCP Protocol
                                  │
                 ┌────────────────┼────────────────┐
                 ▼                ▼                ▼
               Tools           Prompts         Resources
                 │                │                │
                 └────────────────┼────────────────┘
                                  ▼
                         AI Workbench Core
                                  │
            ┌─────────────────────┼─────────────────────┐
            ▼                     ▼                     ▼
      Prompt Registry        Skill Registry       Workflow Registry
                                                        │
                                                        ▼
                                                 Workflow Validator
                                                        │
                                                        ▼
                                                  Workflow Engine
                                                        │
                                  ┌─────────────────────┼──────────────┐
                                  ▼                     ▼              ▼
                               Runs                 Approvals       Artifacts
                                  │
                                  ▼
                          Permission / Tool Layer
                                  │
                                  ▼
                               Sandbox
                                  │
                                  ▼
                              Workspace
```

前端：

```text
Vue Router
  └── /workbench
       ├── Workflows
       ├── Skills
       ├── Prompts
       └── Runs

Workflow Editor
  └── Vue Flow
```

---

## 2. Python 模块规划

```text
mcp_tools_server/workbench/
├── models.py
├── paths.py
├── registry.py
├── prompts.py
├── skills.py
├── workflows.py
├── validation.py
├── engine.py
├── runs.py
├── builtins/
│   ├── prompts/
│   ├── skills/
│   └── workflows/
└── errors.py
```

MCP Tool：

```text
mcp_tools_server/tools/workbench/
├── definitions.py
└── handlers.py
```

不把 Workflow Engine 塞回 `Runtime`。Runtime 只组合 WorkbenchService。

---

## 3. Prompt 模型

```json
{
  "schema_version": 1,
  "id": "frontend-review",
  "name": "前端 UI 审查",
  "description": "检查 Vue UI 一致性与工程规范",
  "arguments": [
    {
      "name": "target",
      "description": "需要审查的页面或目录",
      "required": true
    }
  ],
  "messages": [
    {
      "role": "user",
      "content": "审查 {{target}}，优先检查 UnoCSS、shadcn-vue 与交互一致性。"
    }
  ]
}
```

约束：

- Prompt 不直接拥有执行权限；
- Prompt 参数只做模板替换；
- Prompt 渲染不得执行脚本；
- Prompt ID 在最终合并 Registry 中唯一。

---

## 4. Skill 模型

Skill 使用目录型资源：

```text
skills/reverse-engineering/
├── skill.json
├── SKILL.md
├── templates/
└── references/
```

`skill.json`：

```json
{
  "schema_version": 1,
  "id": "reverse-engineering",
  "name": "旧项目逆向工程",
  "description": "建立架构、行为、接口和测试基线",
  "tool_references": [
    {"provider": "system", "tool_name": "list_files"},
    {"provider": "system", "tool_name": "read_file"},
    {"provider": "system", "tool_name": "search_text"},
    {"provider": "system", "tool_name": "exec_process"}
  ],
  "entry_prompt": "reverse-engineering",
  "artifacts": [
    "architecture.md",
    "behavior-map.md",
    "gap-analysis.md"
  ]
}
```

`SKILL.md` 保存方法论正文。

---

## 5. Workflow Definition

### 5.1 顶层模型

```json
{
  "schema_version": 1,
  "id": "reverse-engineering-flow",
  "name": "旧项目逆向工程",
  "description": "从扫描到行为基线的完整流程",
  "version": 1,
  "entry_node_id": "inspect",
  "nodes": [],
  "edges": [],
  "metadata": {}
}
```

### 5.2 Node 通用结构

```json
{
  "id": "inspect",
  "type": "skill",
  "name": "Repository Recon",
  "position": { "x": 100, "y": 100 },
  "config": {},
  "policy": {
    "approval": "none",
    "on_error": "stop"
  }
}
```

`position` 只服务于可视化布局，执行引擎不能依赖坐标。

### 5.3 第一版 Node 类型

#### prompt

调用 Prompt，生成模型输入模板。

```json
{
  "type": "prompt",
  "config": {
    "prompt_id": "requirements-analysis",
    "arguments": {}
  }
}
```

#### skill

执行一个 Skill 方法。

```json
{
  "type": "skill",
  "config": {
    "skill_id": "reverse-engineering"
  }
}
```

#### tool

显式调用一个 MCP Tool。

```json
{
  "type": "tool",
  "config": {
    "tool_name": "git_diff",
    "arguments": {}
  }
}
```

#### approval

暂停执行等待用户确认。

```json
{
  "type": "approval",
  "config": {
    "title": "确认设计方案",
    "description": "继续后将进入代码修改阶段"
  }
}
```

#### condition

根据上游结构化结果选择 Edge。

第一版只支持受限表达式，不运行 Python/JS。

允许语法：

```text
true
false
path.to.value
!path.to.value
path.to.value == "literal"
path.to.value != "literal"
```

`path.to.value` 只读取 Engine 提供的结构化 values。禁止函数调用、算术、属性执行、Python `eval`、JavaScript `eval` 或任意代码表达式。

#### artifact

声明或保存中间产物。

#### test

执行预定义测试步骤，语义上仍通过 Tool Layer 执行。

### 5.4 Edge

```json
{
  "id": "inspect-to-design",
  "source": "inspect",
  "target": "design",
  "condition": "success"
}
```

第一版 Edge condition 支持：

```text
success
failure
approved
rejected
true
false
```

---

## 6. Workflow Validator

保存前必须验证：

- schema_version 支持；
- id 格式；
- node id 唯一；
- edge id 唯一；
- source / target 存在；
- entry node 存在；
- 普通 Edge 无环；
- node type 已注册；
- Prompt/Skill/Tool 引用存在；
- Tool allowlist 不越过 Runtime 权限；
- approval/condition 配置完整；
- 不允许孤立的不可达执行节点；
- 无 secret 直接写入 Workflow 文件。

Validator 输出：

```json
{
  "ok": false,
  "errors": [],
  "warnings": []
}
```

### 6.1 Workflow Engine 执行边界

第一版 Engine 不包含 LLM Client。Engine 只负责：

- DAG 调度；
- 节点输入/输出状态转换；
- Tool Node 调用 Runtime Tool Layer；
- Prompt/Skill Node 生成结构化 `ModelAction`；
- Condition Node 使用受限表达式本地判定；
- 为后续 Run/Approval 层提供可恢复的 step 结果。

Prompt Node 示例：

```json
{
  "type": "model_action",
  "node_id": "analyze",
  "prompt_id": "project-analysis",
  "messages": [],
  "allowed_tools": []
}
```

Skill Node 示例：

```json
{
  "type": "model_action",
  "node_id": "reverse",
  "skill": {
    "id": "reverse-engineering",
    "method_document": "..."
  },
  "allowed_tools": ["list_files", "read_file", "search_text"],
  "allowed_tool_references": [
    {"provider": "system", "tool_name": "list_files"},
    {"provider": "system", "tool_name": "read_file"},
    {"provider": "system", "tool_name": "search_text"}
  ],
  "messages": []
}
```

模型响应由后续 Workflow Run / continue 机制送回 Engine。Engine Core 不依赖 ChatGPT、Claude 或任何特定模型 Provider。

Tool Node 始终复用现有执行链：

```text
Runtime.call_tool
  -> ToolDispatcher
  -> PermissionSession
  -> PermissionBroker
  -> Sandbox
```

Workflow Engine 不建立第二套 Tool 执行路径。

嵌套 Tool 调用必须继承外层 MCP `RequestContext/principal`。Runtime 使用 request-scoped ContextVar 保存当前 RequestContext，Workbench handler 内部再次调用 `Runtime.call_tool` 时自动继承该上下文，确保 PermissionSession、stored grants 和 session grants 仍绑定到同一 principal。

调度采用显式激活语义：entry node 初始激活；节点完成后按 outcome 沿匹配 Edge 激活后继节点。目标节点第一次被激活后进入 ready，同一 Run 内最多执行一次。普通多入边节点采用 OR merge；AND join 留给未来显式 `join` 节点。

---

## 7. Workflow Run

```json
{
  "schema_version": 1,
  "run_id": "...",
  "workflow_id": "legacy-reverse-engineering",
  "workflow_version": 3,
  "workspace": "/project",
  "status": "waiting_approval",
  "current_node_id": "design-approval",
  "completed_node_ids": ["inspect", "architecture"],
  "node_states": {},
  "artifacts": [],
  "approvals": [],
  "created_at": 0,
  "updated_at": 0
}
```

Run 状态：

```text
pending
running
waiting_model
waiting_approval
succeeded
failed
cancelled
```

Run 创建时保存完整 `workflow_snapshot`。恢复时使用 Snapshot，而不是重新读取 Workflow Registry 中可能已经升级的新版本。

Prompt/Skill 节点进入 `waiting_model` 后，把 `ModelAction` 返回给 MCP Client；AI 完成该节点后通过 `workflow_continue` 回传 `success/failure + output`。

Approval 节点进入 `waiting_approval`。AI 不能使用 `workflow_continue` 自行批准，必须等待 Desktop/UI 的显式用户操作。

### 7.1 Workflow Approval Signed Broker

跨进程审批复用 Desktop Permission Broker 的私有临时目录与签名密钥：

```text
MCP Runtime
  -> signed workflow-approval request
  -> Desktop private broker directory
  -> DesktopAPI / Vue 用户操作
  -> signed workflow-approval response
  -> Runtime workflow_continue consumes response
```

Approval payload 至少绑定：

```text
server_id
run_id
node_id
approval_id
request_id
created_at
expires_at
```

响应同时绑定原 `request_id/run_id/node_id/approval_id`，并经过同一 HMAC secret 验证。Runtime 只接受匹配当前 Run pending approval 的有效响应。

`.coding-tools/runs/<run_id>/run.json` 不具有“证明用户批准”的安全权限，只负责恢复业务状态。

Artifact Node 第一版配置：

```json
{
  "type": "artifact",
  "config": {
    "artifact_id": "architecture",
    "source_node_id": "reverse",
    "format": "json"
  }
}
```

`format` 只支持 `json` / `text`；Artifact 只能写入当前 Run 自己的 artifact 目录。

---

## 8. Vue Flow Editor 设计

### 8.1 页面

路由：

```text
#/workbench/workflows
#/workbench/workflows/:workflowId
#/workbench/skills
#/workbench/prompts
#/workbench/runs
```

第一版可先使用：

```text
#/workbench
```

内部 Tab 管理四种资源，等页面复杂后再拆嵌套路由。

### 8.2 布局

```text
┌──────────────────────────────────────────────────────────────┐
│ Workflow: Project Development          节点库 Inspector 验证 │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│                    Vue Flow Canvas                           │
│                                                              │
│              ○ [ Node ] ○────○ [ Node ] ○                   │
│                                                              │
└──────────────────────────────────────────────────────────────┘

节点库按钮 → 左侧 Sheet 覆盖画布显示
Inspector → 右侧 Sheet 覆盖画布显示
```

Node Library 和 Inspector 不作为常驻网格列占用画布宽度。两者使用基于
shadcn-vue / Reka UI primitive 的非模态 Sheet，在需要时覆盖画布显示；关闭后
画布恢复完整横向空间。点击 Node / Edge 时可以自动打开 Inspector。

Workflow Node 的 Handle 数量由 Node Role 决定：Input 仅提供右侧 source Handle，
Process 提供左侧 target Handle 和右侧 source Handle，Output 仅提供左侧 target Handle。
Handle 使用小圆点并保持圆心落在节点边框线上；Condition、Approval 等分支节点后续
可以扩展为多个语义化 source Handle。

应用主 Sidebar 支持展开和简略两种状态。简略状态仅显示图标，工作台页面取消
常规内容最大宽度限制，并使用更小的左右 padding，使 Vue Flow 成为页面主体。

### 8.3 Node 视觉状态

Workbench 将节点拆成两层概念：

```text
Node Role: input / process / output
Node Kind: prompt / skill / tool / approval / condition / artifact / test
```

Role 只负责画布拓扑和连接能力，不替代 Runtime 的业务节点类型：

```text
input    仅 source Handle；对应唯一 entry_node_id
process  target + source Handle
output   仅 target Handle
```

Input / Output 不是新的 Runtime NodeKind。AI 生成 Workflow 时仍必须使用受支持的
Node Kind，并通过 entry_node_id 与节点角色表达入口和出口语义。

节点业务类型使用不同边框色，颜色来自 shadcn/Tailwind 调色板，用于快速识别
Node Kind，不与运行状态混用：

```text
Prompt      sky
Skill       violet
Tool        blue
Approval    amber
Condition   orange
Artifact    emerald
Test        teal
```

运行状态主要通过背景色和选中 Ring 表达，避免状态色覆盖 Node Kind 的识别色。
Handle 遵循 Vue Flow 默认定位：连接圆点的圆心位于节点边框线上。Handle 不使用
hover 缩放，避免拖拽连接时节点边缘发生视觉跳动。

```text
灰色    idle
蓝色    running
绿色    succeeded
红色    failed
黄色    waiting approval
```

运行时 Vue Flow 只读，不允许修改当前 Run 使用的 Workflow Version。

编辑时保存产生新的 Workflow version。

### 8.4 Workflow Catalog 实时一致性

Desktop Workbench 与 MCP Runtime 共用 Workspace `WorkflowStore`，但运行中的 Runtime
会持有内存 Registry。为避免 Desktop 保存后 AI 仍看到旧列表，Workspace Workflow
Registry 必须在以下 AI 调用前从 `WorkflowStore` 刷新：

```text
workflow_authoring_context
workflow_list
workflow_get
workflow_export
workflow_start
```

刷新只替换 Registry 的 Workspace layer，不替换 Registry 对象本身，因此
`WorkflowRunManager` 等长期持有者仍引用同一个 Registry，同时能看到最新的新增、更新和删除。

Desktop 保存、更新和删除完成后也必须重新读取 Workbench Catalog，使 UI Workflow
下拉列表中的名称、描述、版本、scope、节点数和边数与磁盘中的权威定义一致。

### 8.5 AI 生成

界面增加输入框：

```text
“创建一个旧项目逆向工程流程：先扫描目录，然后分析 API，
生成架构文档，用户确认后执行基线测试。”
```

AI 调用：

```text
workflow_validate
workflow_save
```

保存后 Vue Flow 自动载入同一份 Workflow Definition。

---

## 9. MCP 接口设计

### Prompt Protocol

实现：

```text
prompts/list
prompts/get
```

### Workflow Tools

```text
workflow_list
workflow_get
workflow_validate
workflow_save
workflow_delete
workflow_start
workflow_status
workflow_continue
workflow_cancel
```

### Skill Tools

Skill 本身优先作为 Resource/Registry 数据暴露，不需要为每个 Skill 创建独立 Tool。

可提供：

```text
skill_list
skill_get
```

供不支持 MCP Resources 的 Client 使用。

---

## 10. 安全设计

### 10.1 Workflow 文件不能保存 secret

网络 Token、OAuth Password、API Key 等只能引用 Secret Binding：

```json
{
  "secret_ref": "workspace:openai_api_key"
}
```

第一版 Secret Binding 可以暂不实现，但 Validator 必须拒绝明显的 secret 字段。

### 10.2 AI 保存工作流不等于获得执行权限

```text
workflow_save
```

是配置写入。

执行中的 Tool 调用仍走原 Permission Session。

### 10.3 Git Write

Workflow 不能把 Git Write 变成自动授权。

如果用户当前策略要求显式授权，则 Workflow 执行到 Git Write 节点仍需 Permission Broker。

---

## 11. Built-in Workflow

Workbench 以用户自定义 Workflow 为主，只保留一个默认 Workflow：

### project-development

```text
Spec 驱动开发 Skill
→ 保存交付报告 Artifact
→ 人工确认 Approval
```

`Spec 驱动开发` Skill 内部方法论对应当前项目实际采用且已经验证稳定的主流程：

```text
Requirements
→ Design
→ Tasks
→ Implementation
→ Test & Acceptance
```

Bug Investigation、Code Review、Reverse Engineering、Release Validation 等继续作为 Prompt / Skill 能力存在，但不再默认占用 Workflow 列表。用户需要时可以自由组合成 Workspace Workflow。

---

## 12. Capability Asset Workbench

### 12.1 总体关系

Phase 10 之后的 Workbench 不再从“先画 Workflow 节点”出发，而是先管理能力资产，再由 Workflow 引用资产：

```text
                    User / AI Authoring
                           │
          ┌────────────────┼────────────────┐
          │                │                │
        Prompt           Skill       MCP Connection
          │                │                │
          │                │           discover tools
          │                │                │
          └──────────┬─────┴─────────┬──────┘
                     │               │
                     ▼               ▼
                  Workflow       Tool Catalog
                     │          ┌────┴─────┐
                     │       System      MCP
                     │        Tools      Tools
                     ▼
              Workflow Runtime
```

System Tool 由程序代码实现，并继续复用现有 Capability、Permission Session、Sandbox 和审计规则。

### 12.2 Capability Catalog

Desktop 与 MCP Runtime 应提供统一 Catalog：

```text
PromptCatalog
SkillCatalog
MCPConnectionCatalog
ToolCatalog
WorkflowCatalog
```

ToolCatalog 是聚合视图：

```text
System Tools
  +
Discovered MCP Tools
  =
Effective Tool Catalog
```

Skill 编辑器与 Workflow Tool Node 都从 Effective Tool Catalog 选择能力，禁止各自维护一份静态 Tool 列表。

### 12.3 Asset Reference

Workflow 不复制 Prompt / Skill / MCP Connection 定义，只持有稳定引用。

```json
{
  "type": "skill",
  "config": {
    "skill_id": "frontend-page-replication"
  }
}
```

```json
{
  "type": "prompt",
  "config": {
    "prompt_id": "summarize-change",
    "arguments": {}
  }
}
```

Tool Reference 统一为：

```json
{
  "type": "tool",
  "config": {
    "provider": "system",
    "tool_name": "read_file",
    "arguments": {}
  }
}
```

或：

```json
{
  "type": "tool",
  "config": {
    "provider": "mcp",
    "connection_id": "github",
    "tool_name": "create_pull_request",
    "arguments": {}
  }
}
```

旧 Workflow 中只有 `tool_name` 的 Tool Node 迁移时默认视为 `provider = system`，保证已有定义继续可读。

### 12.4 Prompt Management

Prompt 是应用级 Global Capability Asset，不绑定某个 Workspace，至少包含：

```text
id
name
description
arguments
messages
version
scope
```

GUI 提供创建、编辑、验证、删除；AI 提供完全对应的 Authoring Tools。Prompt 管理页面不显示 Workspace Target。

Prompt 是消息模板，不等于宿主 AI 的 System Prompt。MCP Server 无权覆盖 Host 的 System / Developer Instructions。

### 12.5 Skill Management

Skill 是应用级 Global Capability Asset，也是用户最主要的 AI Capability Asset，至少包含：

```text
id
name
description
entry_prompt
method_document
tool_references
artifacts
version
scope
```

`tool_references` 直接保存稳定 Tool Reference，以支持 System Tool 和 MCP Tool 同时存在。

Skill 编辑器必须允许用户：

```text
选择入口 Prompt
编辑 Method / Instructions
选择允许的 System Tools
选择允许的 MCP Tools
定义预期 Artifacts
```

Skill 管理页面不选择 Workspace Target。具体在哪个项目中使用 Skill，由 Workflow 的 Target / Run Context 决定。

### 12.5.1 Global Capability Store

新建或修改 Prompt / Skill 写入应用级 Global Store：

```text
Application Data / Workbench
├── prompts
└── skills
```

Runtime Catalog 合并顺序：

```text
Built-in
  ↓
Global
```

Prompt / Skill 不存在 Workspace 作用域；Workflow 仍然保存在目标 Workspace。

### 12.6 MCP Connection Management

MCP Connection 是应用级 Global Capability Asset，不绑定 Workspace。Secret 不允许以明文进入 Connection JSON。

当前字段：

```text
id
name
transport = http | stdio
endpoint / command
arguments
environment
environment_refs
headers
header_refs
enabled
version
tools[]
last_discovered_at
last_error
```

Secret Reference 第一阶段支持 `env:NAME`。`environment` / `headers` 只允许非敏感值；Password、Token、Authorization、API Key 等必须进入对应 `*_refs` 字段。

连接成功后执行 discovery，将外部 Tool 的 `name`、`description`、`inputSchema` 缓存进 Connection，并动态组成 Effective Tool Catalog：

```text
System Tool Definitions
        +
Enabled MCP Connection discovered tools
        ↓
Effective Tool Catalog
```

稳定 key：

```text
system:<tool_name>
mcp:<connection_id>:<tool_name>
```

Skill 可以从 Effective Tool Catalog 选择 System / MCP Tool。Connection 禁用时 MCP Tool 从 Effective Catalog 消失但引用保留；Connection 被 Skill 引用时禁止删除。

AI 侧提供：

```text
mcp_connection_list
mcp_connection_get
mcp_connection_validate
mcp_connection_save
mcp_connection_delete
mcp_connection_test
mcp_connection_discover_tools
```

HTTP Test / Discovery / Tool Call 需要 Network permission；stdio Test / Discovery / Tool Call 需要显式允许启动用户配置的外部进程。stdio 协议读取使用有界 timeout，不允许坏掉的 MCP Server 无限阻塞 Runtime。

Phase 13 后 Effective Tool Catalog 中的 System / MCP Tool 都可以被 Workflow Tool Node 引用，`workflow_executable = true`。外部 MCP Tool 不绕过 Runtime，而是通过内部 control-plane adapter 执行：

```text
Workflow Tool Node
        ↓ ToolReference(provider=mcp)
Workflow Engine
        ↓
Runtime.call_tool("mcp_connection_call_tool")
        ↓
Permission Session / Broker
        ↓
MCPConnectionService.call_tool
        ↓
external MCP tools/call
```

HTTP Tool Call 在真正执行前先使用无副作用 `server/discover` 判定 modern / legacy 协议，不能在一个有副作用的 `tools/call` 失败后再 fallback 并重复调用。

Skill 的 MCP Tool allowlist 通过 `allowed_tool_references` 进入 ModelAction；如果存在 MCP Tool Reference，宿主可使用内部 `mcp_connection_call_tool` adapter 按 reference 调用。

### 12.7 Dynamic Node Library

Node Library 按语义分组：

```text
AI
  Skill

Actions
  Tool

Flow Control
  Condition
  Approval
  Artifact

Advanced
  Prompt
```

添加 Skill / Prompt / Tool Node 后，Inspector 从对应 Catalog 选择实际资产。

Tool Inspector 根据 Provider 动态显示：

```text
System
  -> 选择内置 Tool

MCP
  -> 选择 Connection
  -> 选择该 Connection discover 出来的 Tool
```

Workflow Node Kind 固定为 `prompt / skill / tool / approval / condition / artifact`。Tool Node 必须显式保存 `provider` 与 `tool_name`；MCP Tool 还必须保存 `connection_id`。

### 12.8 Authoring Parity

所有资产都遵循同一条链路：

```text
GUI ───────────────┐
                   ▼
             Domain Service
                   │
AI MCP Tool ───────┘
                   │
          Validate / Version
                   │
              Store / Registry
                   │
              Catalog Refresh
```

AI 不允许通过直接写 `.coding-tools` 文件绕过 Validator / expected_version / Secret Policy。

### 12.9 Workflow Discovery

Workflow 是给 AI 调用的用户能力，因此 Phase 14 起 Workflow Summary 固定包含：

```text
id
name
description
version
scope
inputs_schema
tags
node_count
edge_count
```

`description` 与 `inputs_schema` 用于 AI 判断“这个 Workflow 能做什么、调用时需要什么参数”。`tags` 用于轻量主题匹配，不替代 description。

运行链路：

```text
workflow_list
  ↓
AI 根据 description / tags 选择 Workflow
  ↓
AI 按 inputs_schema 构造 inputs
  ↓
workflow_start
  ↓
WorkflowRunManager 校验 inputs_schema
  ↓
Engine Start
```

缺省 schema 可以使用开放 object；需要稳定输入契约的 Workflow 应声明更具体的 properties / required。

Workflow description 在 `WorkflowDefinition.from_mapping` 领域入口就必须非空，因此 `workflow_validate / workflow_save / Desktop / Runtime` 使用同一合法性标准。

Vue Flow 的 Graph model 只转换 Node/Edge，但 Editor state 同时持有并 round-trip：

```text
inputs_schema
tags
metadata
```

因此 AI 创建后再由 GUI 保存，不会丢失非画布 discovery 数据。

### 12.10 安全边界

Phase 10+ 继续坚持：

```text
用户 / AI 可以定义能力组合
        ≠
用户 / AI 可以扩大 Runtime 权限
```

Skill Tool Allowlist、Workflow Tool Node、外部 MCP Tool 都只能在当前 Runtime / Connection 已允许的能力集合中收紧或引用，不能越过 Permission Broker、Sandbox 和 Secret Binding。

### 12.11 Visual Workflow Editor Productization

Phase 15 的 Editor 仍然只有一个持久化模型：`WorkflowDefinition`。Vue Flow Canvas、Inspector、Undo / Redo 和 AI Authoring 都围绕它工作，不创建 UI-only Workflow Schema。

参数编辑采用两层结构：

```text
Prompt arguments / Tool input_schema
              ↓
      SchemaValueEditor
              ↓
    node.config.arguments

Workflow inputs_schema
              ↓
     ObjectSchemaBuilder
              ↓
WorkflowDefinition.inputs_schema
```

可视化表单覆盖 string / integer / number / boolean / enum 和常用 object/array JSON；Advanced JSON 继续作为复杂 Schema 和高级配置入口。

Validation 通过 `WorkflowValidationIssue.subject` 定位 Node / Edge。Canvas 只负责展示错误 ring / count，具体错误仍来自 Python Validator，避免前端复制业务校验规则。

Undo / Redo 以序列化 Workflow Definition snapshot 实现，最多保留 100 个编辑状态。Duplicate Node 复制 config / policy 但生成新 Node ID，并移除拓扑角色，避免复制 entry/output 约束造成非法 Graph。

### 12.12 Recovery and Version Boundary

0.3.x 发布前内部试验格式不进入正式 migration pipeline。Prompt / Skill 只从 Global Store 加载，Workflow 只从 Workspace Store 加载；Definition 必须携带当前 `schema_version`。测试环境中的旧数据直接清理后重新初始化。

损坏 Catalog 条目进入 `.quarantine`，但 future schema 永远留在原位置，仅记录 skip。MCP rediscovery 失败保留最后一次成功 discovered tools，并更新 `last_error`，避免暂时网络故障让引用该 Connection 的 Skill / Workflow 永久丢失能力定义。


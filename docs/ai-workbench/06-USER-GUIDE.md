# AI Workbench 用户指南

## 1. 定位

AI Workbench 是 MicroMatrix Workbench 在原子 Tool 之上的工程编排层。

核心资源：

```text
Prompt
Skill
Workflow
Workflow Run
```

- Prompt：定义 AI 的工作规则与输出方式；
- Skill：定义某类工程任务的方法论、允许工具和产物；
- Workflow：用 DAG 编排 Prompt / Skill / Tool / Approval / Condition / Artifact / Test；
- Workflow Run：保存一次真实执行的状态、产物、审批和错误。

最终执行权限仍由 Permission Broker 和 Safe Sandbox 决定。

---

## 2. AI 工作台

桌面端通过 `AI 工作台` 进入 Workflow 编辑器。

编辑器基于 Vue Flow，但 Vue Flow 只是可视化层。权威数据始终是：

```text
Workflow Definition JSON
```

因此 AI 修改和用户拖拽修改操作的是同一份 Workflow Definition。

基本布局：

```text
┌────────────────────────────────────────────────────────────┐
│ 节点库                                        Inspector    │
│                                                            │
│                    Vue Flow Canvas                         │
│                                                            │
│              ○ [ Node ] ○────○ [ Node ] ○                 │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

`节点库` 和 `Inspector` 默认不常驻占用画布宽度：

- 点击 `节点库`，从左侧打开 Sheet；选择节点后 Sheet 自动收起。
- 点击 Node 或 Edge，会从右侧打开 Inspector。
- 两个 Sheet 都覆盖在 Canvas 上，不会重新压缩 Canvas。
- Node 左侧圆点是输入 Handle，右侧圆点是输出 Handle，从输出点拖到另一个
  Node 的输入点即可建立 Edge。

应用左侧主菜单也可以通过顶部按钮切换为简略模式。简略模式只保留菜单图标，
适合在编辑大型 Workflow 时进一步扩大画布可用区域；该状态会在本地保存。

---

## 3. Workbench Target

Workbench Target 是一个 Service / Workspace Profile，而不是任意文件夹。

Target 决定：

```text
server_id
Workspace
Workflow Registry
Run History
Approval identity
```

Prompt / Skill 是应用级全局能力资产，不需要在管理页面选择 Target。每个 Target 的 Runtime
都会加载同一份 Global Prompt / Skill Catalog；Target 只在 Workflow 编排、保存和运行时指定。

Direct Service 和 Gateway Member 都可以成为 Target。

### 3.1 Global Capability Assets

AI 工作台侧栏包含三类全局能力管理页：

```text
Prompts
Skills
MCP 服务
```

这些页面都不选择 Workspace Target。

`MCP 服务` 用于配置外部 MCP Server。HTTP 填写 Endpoint；stdio 填写 Command 与 Arguments。敏感环境变量或 Header 不直接填写真实值，而使用 Secret Reference，例如：

```json
{
  "Authorization": "env:GITHUB_MCP_AUTH"
}
```

保存 MCP 服务后可以先执行 `测试连接`，再执行 `发现 Tools`。Discovery 会保存外部 Tool 的名称、说明和 Input Schema，并把它们加入 Effective Tool Catalog。Skill 的 `Allowed Tools` 会同时显示：

```text
System Tools
MCP Tools
```

禁用一个 MCP 服务不会删除已有 Skill 引用，只会暂时让对应 Tool 不可用。仍被 Skill 引用的 MCP 服务不能直接删除，必须先移除 Skill 引用。

Phase 13 起，已启用且完成 Discovery 的 MCP Tool 可以直接被 Workflow Tool Node 引用和执行。HTTP MCP 调用仍受 Network permission 约束，stdio MCP 调用仍受外部可执行程序授权约束；Workflow 不会获得额外权限。

---

## 4. Workflow 编辑

Node Library 按用途分组：

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

画布同时提供三种节点角色。角色和上面的业务节点类型不是同一个概念：

```text
输入节点   只有输出连接点，是 Workflow 的 entry_node_id
处理节点   同时拥有输入、输出连接点
输出节点   只有输入连接点
```

例如一个 `skill` 既可以作为输入节点，也可以作为普通处理节点；`artifact` 也可以作为
处理中间节点或输出节点。节点角色决定连线方向，业务节点类型决定 Runtime 如何执行。

不同业务节点使用不同边框色，便于在大画布中快速识别：Prompt 使用 sky、Skill 使用
violet、Tool 使用 blue、Approval 使用 amber、Condition 使用 orange、Artifact 使用
emerald。连接点是圆心位于节点边框线上的小圆点，悬浮时不会放大。

节点坐标只用于 Vue Flow 布局。执行顺序只由 Edge 决定。

第一版 Workflow 必须是 DAG，不允许普通 Edge 形成环。

Workspace Workflow 可以在编辑器顶部直接删除；内置 Workflow 和尚未保存的新 Workflow
不可删除。保存、更新或删除 Workspace Workflow 后，编辑器会立即刷新 Workflow 列表。
运行中的 MCP Runtime 在 AI 执行 `workflow_list`、`workflow_get`、
`workflow_authoring_context`、`workflow_export` 或 `workflow_start` 时也会重新读取
Workspace Workflow，因此无需重启 MCP Server，AI 就能看到最新的 Workflow 名称、说明和版本。

Phase 14 起，Workflow 还包含 discovery metadata：

```text
description   说明什么时候应该使用这个 Workflow
inputs_schema 定义 workflow_start.inputs
tags          用途标签
```

在画布空白处点击后打开 Inspector，可以编辑这三个字段。AI 创建的 Workflow 用 GUI 打开再保存时，这些字段以及原有 metadata 都会保留。

Phase 15 起，Workflow Editor 主要操作不再要求手写 JSON：

- Node Library 顶部可以搜索 Skill / Tool / Approval 等节点类型；
- Prompt / Skill / Tool Inspector 可以按名称、ID 或说明搜索能力；
- Prompt / Skill arguments 会根据关联 Prompt arguments 自动生成输入框；
- System / MCP Tool arguments 会根据 Tool `input_schema` 自动生成输入框；
- Workflow Inputs Schema 使用可视化参数 Builder，可以维护参数名、类型、说明、Required 和 Additional Properties；
- 复杂 object / array 或高级 Schema 仍可展开 `Advanced JSON` / `Advanced JSON Schema`；
- 顶部提供 Undo / Redo；选中 Node 后可 Duplicate；删除 Node / Edge / Workflow 前会确认；
- 点击“验证”后，有问题的 Node 会直接显示错误徽标，Inspector 会显示对应错误内容；引用已删除 Prompt / Skill 或禁用 MCP Tool 时也会就地提示；
- 空 Workflow 可以直接点击画布中央入口打开 Node Library。

多入边使用 OR merge：目标节点第一次收到有效 activation 后进入 ready，同一 Run 中最多执行一次。需要 AND Join 时未来使用显式 Join Node。

---

## 5. 节点配置

Prompt Node：

```json
{
  "prompt_id": "project-analysis",
  "arguments": {}
}
```

Skill Node：

```json
{
  "skill_id": "reverse-engineering",
  "arguments": {}
}
```

System Tool Node：

```json
{
  "provider": "system",
  "tool_name": "git_diff",
  "arguments": {}
}
```

MCP Tool Node：

```json
{
  "provider": "mcp",
  "connection_id": "github",
  "tool_name": "create_issue",
  "arguments": {
    "title": "Bug"
  }
}
```

旧 Workflow 中只有 `tool_name` 的 Tool/Test Node 会自动按 `provider = system` 解释，不需要手工迁移。

Workflow 内禁止调用 `workflow_* / prompt_* / skill_* / mcp_connection_*` 等 Workbench 控制面 Tool，避免业务流程修改控制面或形成递归编排。

Approval Node 到达后必须等待 Desktop 用户明确批准或拒绝。AI 无法通过 `workflow_continue` 参数自行批准。

Condition Node 第一版只支持：

```text
true
false
path.to.value
!path.to.value
path.to.value == "literal"
path.to.value != "literal"
```

不运行 Python、JavaScript 或 `eval`。

Artifact Node 只保存已有节点输出为稳定 Artifact，不允许配置任意 Workspace 写入路径。

---

## 6. Validation 与版本冲突

保存前以后端 Validator 为准。

Validator 检查：

- Node / Edge ID；
- entry node；
- DAG 与不可达节点；
- Prompt / Skill / Effective Tool 引用；
- Approval / Condition / Artifact 配置；
- secret 明文；
- Workbench 控制面 Tool 调用；
- MCP Connection 是否启用、Tool 是否已完成 Discovery。
- 新建/保存的 Workspace Workflow 是否填写 description；
- inputs_schema 是否是 object 输入契约。

保存使用：

```text
expected_version
```

如果 AI 和用户同时修改同一 Workflow，旧版本保存会返回 `WORKFLOW_VERSION_CONFLICT`，不会静默覆盖较新版本。

---

## 7. 自然语言生成 Workflow

可以直接对已连接 MicroMatrix Workbench 的 AI 说：

```text
给当前项目创建一个逆向工程工作流：
先扫描项目，然后建立架构和 API 地图，生成报告，
人工确认后再进入测试基线。
```

如果 Prompt / Skill 已经存在，AI 标准流程：

```text
workflow_authoring_context
        ↓
workflow_get（修改已有 Workflow 时）
        ↓
生成 Draft
        ↓
workflow_validate
        ↓
workflow_save(expected_version)
```

如果用户要求的是一套新的能力，AI 可以完整创建：

```text
prompt_list / skill_list
        ↓
prompt_save（缺少时）
        ↓
skill_save（缺少时）
        ↓
workflow_authoring_context
        ↓
workflow_validate
        ↓
workflow_save(expected_version=0)
        ↓
workflow_list
        ↓
根据 inputs_schema 调用 workflow_start
```

`workflow_list` 已直接返回 description、inputs_schema 和 tags，因此 AI 可以先判断“有哪些 Workflow、各自能做什么、需要什么参数”，不必逐个读取完整 Graph。

例如：

```json
{
  "description": "分析指定项目模块并产出审查结果",
  "inputs_schema": {
    "type": "object",
    "properties": {
      "module": { "type": "string" }
    },
    "required": ["module"],
    "additionalProperties": false
  },
  "tags": ["analysis", "review"]
}
```

如果 `workflow_start.inputs` 缺少 `module`，Runtime 会拒绝启动，而不是把 schema 只当作说明文字。

也可以直接说：

```text
在测试前加一个人工确认。
```

```text
把安全审计移动到代码修改之前。
```

```text
删除 Release 节点并重新连接流程。
```

保存后 Vue Flow 读取的是同一份 Definition。

---

## 8. Workflow Run

第一版不内置独立 LLM Provider。

因此：

- Tool / Condition / Artifact 等节点由 Engine 本地处理；
- Prompt / Skill Node 产生 `ModelAction`，交给当前 MCP AI Client；
- AI 处理完成后通过 `workflow_continue` 回传结果；
- Approval 只能由 Desktop 用户处理。

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

Run 保存创建时的不可变 Workflow snapshot，因此 Workflow 后续升级不会改变历史 Run。

---

## 9. 人工审批

Approval 使用 Desktop-owned signed IPC：

```text
MCP Runtime
  ↓ signed request
Desktop private broker directory
  ↓
Desktop UI
  ↓ 用户批准 / 拒绝
signed response
  ↓
Runtime consume
```

请求和响应使用 HMAC 签名。Workspace 文件不作为人工 Approval 的真相来源，因此 AI 不能通过写 `.micromatrix-workbench` 文件伪造人工批准。

---

## 10. Built-in Workflows

Workflow 主要由用户自行创建。系统仅保留一个默认示例：

```text
Project Development
```

它使用 `Spec 驱动开发` Skill，覆盖 Requirements、Design、Tasks、Implementation、Test & Acceptance，并在最后保存交付 Artifact、等待人工确认。

Bug Investigation、Reverse Engineering、Code Review、Release Validation 等仍然可以作为 Skill 被用户组合进自己的 Workflow，但不会默认出现在 Workflow 列表中。

详细说明见 `05-BUILTIN-WORKFLOWS.md`。

---

## 11. Import / Export

MCP 提供：

```text
workflow_export
workflow_import
```

Import 仍然经过当前 schema/version 校验、secret 检查、Validator 和 `expected_version`，不会绕过保存规则。

---

## 12. 资源目录与恢复

应用级 Global Capability 资源：

```text
Application Data / Workbench/
├── prompts/
├── skills/
└── mcp-connections/
```

Workspace 资源：

```text
<workspace>/.micromatrix-workbench/
├── workflows/
└── runs/
```

Prompt / Skill 的作用域优先级：

```text
Global > Built-in
```

Workflow 的作用域优先级：

```text
Workspace > Built-in
```

Workbench 0.3.x 发布前的内部试验格式不作为正式兼容对象；当前 Definition 必须显式使用当前 `schema_version`，Workflow 必须提供 description，Skill 必须使用 `tool_references`，Tool Node 必须显式声明 provider。

损坏的 Prompt / Skill / MCP Connection / Workflow Catalog 条目会被移动到对应目录的 `.quarantine` 并记录 warning，不会拖垮整个 Runtime，也不会删除原始字节。future schema 数据只会跳过，不会 quarantine，防止旧版本修改新版本数据。Workflow Run 的损坏记录仍按 Run recovery 规则跳过并保留原文件。

MCP Connection 如果临时断网或 discovery 失败，会保留上一次成功发现的 Tool Catalog，并记录 `last_error`。恢复连接后直接再次点击“发现 Tools”即可刷新，不需要重新创建 Connection。

---

## 13. 权限模型

Workflow 不会获得额外超级权限。

执行链仍然是：

```text
Workflow / Skill capability references
        ↓
Runtime Permission Profile
        ↓
Permission Session
        ↓
Permission Broker
        ↓
Sandbox
```

Git Write、destructive command 等操作继续遵守原有权限 Contract。

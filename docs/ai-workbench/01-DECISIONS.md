# AI Workbench 决策文档

## 1. 背景

当前 MicroMatrix Workbench 已经解决了 AI 安全访问本地 Workspace 的基础能力：

- 文件读取与修改；
- Git 只读能力；
- 进程执行；
- Toolchain 发现；
- Permission Broker；
- Safe Sandbox；
- 多 Workspace Runtime。

但完整编码工作还缺少“方法与编排”层。不同任务需要不同的提示词、工作方法、阶段约束、审批节点、测试策略与中间产物，仅继续增加原子 MCP Tool 无法覆盖这些场景。

典型需求包括：

- Spec 驱动开发；
- Bug 调查；
- 旧项目逆向工程；
- 功能复刻；
- 安全审计；
- Release 检查；
- 自定义 Prompt；
- 自定义 Skill；
- 可视化 Workflow；
- AI 通过自然语言生成或修改 Workflow。

因此决定新增 **AI Workbench / Orchestration Layer**。

---

## 2. 核心决策

### ADR-001：保留 Tool 原子化，在 Tool 之上增加 Orchestration Layer

Tool 继续回答“AI 能做什么”，不新增 `reverse_engineer_project()`、`develop_feature()` 这类巨型 Tool。

新增四类一等资源：

```text
Prompt
Skill
Workflow
Workflow Run
```

关系：

```text
Prompt       定义模型应如何思考和输出
Skill        定义处理某类问题的方法、约束、模板和允许工具
Workflow     编排多个步骤、Skill、Prompt、审批和条件
Workflow Run 保存一次真实执行的状态、结果和历史
```

### ADR-002：Workflow Definition 是唯一权威数据，Vue Flow 只是编辑器

工作流不能把 Vue Flow 的组件状态作为持久化真相。

权威模型：

```text
Workflow Definition JSON
```

Vue Flow 负责：

- 展示节点与边；
- 拖拽调整布局；
- 创建/删除连接；
- 编辑节点配置；
- 将用户操作同步回 Workflow Definition。

Workflow Engine 和 MCP Tool 只依赖 Workflow Definition，不依赖 Vue Flow。

因此：

```text
Vue Flow UI ─┐
             ├─> Workflow Definition ─> Validator ─> Engine
AI Tool ─────┘
```

### ADR-003：采用 Vue Flow 作为前端 Workflow Editor

前端安装：

```text
@vue-flow/core
```

采用原因：

- Vue 3 原生；
- TypeScript；
- Nodes / Edges 模型与 Workflow Graph 天然匹配；
- 支持拖拽、缩放、选择、连接；
- 支持自定义 Node / Edge；
- 适合后续加入 MiniMap、Controls、节点状态等能力。

Vue Flow 版本不得决定 Workflow Schema 版本。

### ADR-004：AI 和用户操作同一套 Workflow API

禁止维护“AI 工作流”和“GUI 工作流”两套逻辑。

统一 API：

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

用户在 Vue Flow 中保存和 AI 通过 MCP Tool 生成，最终都进入同一个 Store 与 Validator。

### ADR-005：Prompt 原生支持 MCP Prompts

Prompt Registry 除桌面 UI 外，还通过 MCP 暴露：

```text
prompts/list
prompts/get
```

Prompt 可以包含参数，支持 Built-in / Global / Workspace 三层覆盖。

### ADR-006：资源作用域采用 Built-in / Global / Workspace

优先级：

```text
Workspace > Global > Built-in
```

规划目录：

```text
用户级：
<settings>/ai-workbench/
├── prompts/
├── skills/
├── workflows/
└── runs/

项目级：
<workspace>/.micromatrix-workbench/
├── prompts/
├── skills/
└── workflows/
```

Built-in 资源由程序包提供，只读。

### ADR-007：Skill/Workflow 只能收紧权限，不能扩大 Runtime 权限

执行权限顺序：

```text
Workflow / Skill allowlist
        ↓
Runtime Permission Profile
        ↓
Permission Broker
        ↓
Sandbox
```

如果 Skill 声明仅允许 `read_file`、`search_text`，则它可以主动缩小能力。

如果 Skill 声明 dangerous，不得突破当前 Runtime 的 Safe/Trusted 上限。

### ADR-008：Workflow Run 必须可恢复、可审计

每次运行持久化：

- run_id；
- workflow_id / workflow_version；
- workspace；
- 当前节点；
- 已完成节点；
- 节点输出；
- artifacts；
- approvals；
- errors；
- started_at / updated_at；
- tool invocation summary。

不能只把执行进度保存在前端内存。

### ADR-009：第一版 Workflow 默认是 DAG

第一版禁止普通 Edge 形成环，降低死循环和恢复复杂度。

未来需要循环时新增显式 `loop` 控制节点，并必须定义：

```text
max_iterations
exit_condition
```

### ADR-010：AI 生成 Workflow 采用“生成 -> 验证 -> 预览 -> 保存”

AI 不直接执行未验证的 Graph。

标准流程：

```text
自然语言需求
  ↓
AI 生成 Workflow Draft
  ↓
workflow_validate
  ↓
Vue Flow 预览 / 用户确认
  ↓
workflow_save
  ↓
workflow_start
```

对于纯只读 Workflow，可允许用户配置“保存后直接运行”；默认仍要求显式启动。

### ADR-011：AI Workbench 的领域顺序固定为 Prompt -> Skill -> Workflow -> Run

开发顺序必须服从领域依赖，而不是服从 UI 可见程度。

```text
Tool Framework（已有）
        ↓
Prompt Registry
        ↓
Skill Registry
        ↓
Workflow Definition / Registry
        ↓
Workflow Engine
        ↓
Workflow Run / Artifact / Approval
        ↓
Vue Flow Editor
        ↓
AI Natural Language Authoring
```

允许提前建立下一阶段的最小数据骨架用于验证接口，但不得在前一阶段未验收时继续深化下一阶段实现。

### ADR-012：阶段门禁优先于连续编码

每个 Phase 必须先满足文档中定义的完成标准和测试点，才能进入下一 Phase。

如果实现过程中发现领域模型需要调整：

1. 先更新决策/领域模型文档；
2. 记录变更原因与兼容影响；
3. 更新实施计划；
4. 再修改代码。

禁止以“先把页面做出来再补模型”的方式推进 AI Workbench。

### ADR-013：第一版 Workflow Engine 不内置 LLM，Prompt/Skill 是外部模型执行边界

当前 MicroMatrix Workbench 的 AI 模型由 MCP Client 提供，桌面程序本身没有内置模型 Provider。第一版 Workflow Engine 因此不得假装自己能够直接“执行 Prompt/Skill”。

节点语义固定为：

```text
Tool Node
  -> Engine 通过 Runtime.call_tool 执行

Prompt Node
  -> Engine 渲染 Prompt 并产生 Model Action
  -> 由当前 MCP Client/AI 执行

Skill Node
  -> Engine 组合 Skill 方法论、entry_prompt、tool_references
  -> 产生 Model Action
  -> 由当前 MCP Client/AI 执行

Approval Node
  -> 暂停等待用户

Condition Node
  -> Engine 使用受限表达式本地判定
```

后续如果产品增加内置 AI Provider，只能新增 `ModelExecutor` Adapter；不得改变 Workflow Definition 的节点语义。

### ADR-014：第一版 DAG 采用显式激活语义

节点完成后，根据 outcome 选择匹配的 Edge，并激活对应 target。目标节点第一次被激活后进入 ready，同一 Run 内最多执行一次。

多个入边默认采用 OR merge，不隐式等待所有上游完成。未来如果需要 AND join，必须新增显式 `join` 节点并定义等待集合。

### ADR-015：Run 保存不可变 Workflow Snapshot

当前 Workflow Store 只保存每个 Workflow 的最新版本，因此 Run 不能仅保存 `workflow_id + version` 后在恢复时重新读取最新 Definition。

每个 Workflow Run 创建时必须保存当时的完整 Workflow Snapshot。Definition 后续升级、覆盖或删除都不能改变已经存在的 Run 语义。

### ADR-016：人工 Approval 不允许由 AI 自行批准

`workflow_continue` 只用于回传 Prompt/Skill ModelAction 的执行结果。

当 Run 处于 `waiting_approval` 时，MCP AI Tool 不能传 `approved=true` 绕过人工边界。Approval 必须通过 Desktop/UI 的用户操作通道解决；未来如果接 MCP Elicitation，也必须有真实用户响应证据。

### ADR-017：Workflow Tool Node 禁止调用 `workflow_*` 控制工具

Workflow Definition 中的 Tool Node 不能调用 `workflow_start`、`workflow_continue`、`workflow_retry`、`workflow_save` 等 `workflow_*` 控制工具，避免递归 Run、循环创建 Run 和绕过 Engine 生命周期。

这些工具只允许由 Workbench 外层 AI/UI 控制面调用。

### ADR-018：Workflow Approval 复用 Desktop Signed Broker，不写入 Workspace 决策文件

Workflow Runtime 运行在 MCP 子进程，Vue/pywebview 运行在 Desktop 主进程。人工审批必须跨进程，但不能把“approved/rejected”作为普通 Workspace 文件写入 `.micromatrix-workbench/runs/`，否则拥有 Workspace 写权限的 AI 也可能伪造用户决策。

第一版复用现有 Desktop Permission Broker 的私有目录、随机 32-byte secret、HMAC 签名和 `server_id` 隔离，增加独立的 Workflow Approval 消息类型：

```text
<request-id>.workflow-approval.request.json
<request-id>.workflow-approval.response.json
```

规则：

- Runtime 到达 approval node 后发布签名 request；
- Desktop 只展示签名有效、server_id 匹配的 request；
- 用户在 Desktop UI 明确批准/拒绝后，Desktop 写签名 response；
- `workflow_continue` 在 `waiting_approval` 状态下只能消费一个已经存在且签名有效的 response；
- `workflow_continue` 不接受 `approved` / `decision` 参数；
- Workspace 中的 `run.json` 只保存审批状态与 broker request_id，不作为用户决策的可信来源；
- Desktop 重启导致 broker 目录变化时，Runtime 对仍在 `waiting_approval` 的 Run 重新发布 request。

Permission Request 与 Workflow Approval 使用同一安全 IPC 基础设施，但文件类型、payload kind 和业务语义保持独立。

### ADR-019：Desktop Workbench 以 Service / Workspace Profile 作为编辑目标

Prompt、Skill、Workflow 文件本质上是 Workspace-scoped，但 Desktop UI 不直接让用户输入任意 Workspace Path 作为 Workbench 身份。Workbench Target 使用现有 Service/Profile：

```text
Direct Service
  -> server:<server_id>

Service with Profiles
  -> gateway:<service_id>:<server_id>
```

Target 至少包含：

```text
target_id
server_id
service_name
profile_name
workspace
running
```

原因：

- Run 与 Approval 最终需要绑定具体 Runtime Profile；
- 同一 Workspace 可以被多个服务引用，不能只用路径代表运行身份；
- Desktop 可以基于 Profile 配置做更准确的 Tool/feature validation；
- 前端不直接把任意文件系统路径传给 Workbench 写接口。

Workbench 的持久化资源仍写入目标 Profile 对应 Workspace 的 `.micromatrix-workbench/`，Target 只是 Desktop 控制面身份，不改变资源的 Workspace scope。

### ADR-020：AI 与 Vue Flow 共享 Optimistic Concurrency Contract

AI 和 Vue Flow 都能修改同一份 Workflow Definition，因此保存必须携带 `expected_version`。

Workspace Workflow Store 当前版本定义为：

```text
不存在 -> 0
第一次保存 -> 1
后续保存 -> N + 1
```

保存条件：`expected_version == 当前 Workspace Workflow version`。否则返回版本冲突，调用方必须重新读取 Definition 后再合并修改。

系统不再提供 Built-in Workflow，因此新建 Workspace Workflow 时当前版本统一从 `0` 开始，不存在 Built-in Override 语义。

禁止 last-write-wins 静默覆盖。

### ADR-021：自然语言 Authoring 由 MCP AI Client 完成，Server 提供 Authoring Context

第一版不在 Desktop 内再嵌一个模型。AI 自然语言生成/修改流程固定为：

```text
workflow_authoring_context
  -> 可选 workflow_get
  -> AI 生成/修改 Definition
  -> workflow_validate
  -> workflow_save(expected_version)
```

Server 提供 Prompt/Skill/Tool/Node/Edge 约束和安全规则；AI 负责把用户自然语言转换成 Definition。

### ADR-022：Workflow 与 Skill 均不提供默认资产

AI Client 本身已经具备规划和多步执行能力，因此 AI Workbench 不应通过 Built-in Workflow 或 Built-in Skill 固化一套系统预设的“AI 工作方式”。Workflow 的产品定位是让用户把自己的可重复流程显式化、保存、编辑并交给 AI 调用；Skill 则由用户或 AI 根据实际工作方法创建。

系统启动时不注入任何默认 Skill，也不注入 `project-development` 或其他默认 Workflow。新 Runtime / 新 Workspace 在用户尚未创建资产时，对应 Catalog 应为空。

文档、测试和演示若需要示例流程，必须显式创建 fixture，不得通过产品运行时 Built-in Registry 提供。删除用户资产后也不存在 Built-in fallback。

### ADR-023：Workbench 资产模型固定为 Prompt / Skill / MCP Connection / Workflow，System Tool 由程序提供

AI Workbench 不再把所有能力都视为 Workflow 节点本身，而是先建立可管理的能力资产，再由 Workflow 引用这些资产。

资产所有权固定为：

```text
Prompt          用户 / AI 可创建、编辑、删除
Skill           用户 / AI 可创建、编辑、删除
MCP Connection  用户 / AI 可配置、更新、删除
Workflow        用户 / AI 可创建、编辑、删除
System Tool     程序提供，只读，不允许用户改写实现
```

其中：

- Prompt 是可复用的模型指令模板；
- Skill 是方法论、入口 Prompt、Tool Allowlist 与 Artifact 约束组成的 AI 能力包；
- MCP Connection 是外部能力来源，连接后通过 discovery 产生可引用的 MCP Tools；
- System Tool 是 MicroMatrix Workbench 自身实现的受权限控制原子能力；
- Workflow 负责引用和编排上述能力，不复制其完整定义。

第一阶段不开放任意 Python / JavaScript / Shell Custom Tool。未来如果需要用户参与 Tool 定义，优先从受控 Tool Preset / Command Preset 开始，而不是直接开放任意代码执行。

### ADR-024：MCP 是 Tool Provider，不是新的 Workflow Node Kind

MCP 表达“能力从哪里来”，Tool 表达“实际执行什么动作”。因此 Workflow 不增加 `mcp` Node Kind。

统一 Tool Reference 采用 Provider 模型：

```text
Tool
├── provider = system
│   └── tool = read_file / search_text / apply_patch / ...
└── provider = mcp
    ├── connection_id = github
    └── tool = create_pull_request
```

Workflow Node Library 只展示可执行 Tool，不要求用户先放置一个 MCP Node 再连接 Tool Node。

MCP Connection 的 transport、endpoint、command、environment、secret reference 等连接信息属于 Connection Asset，不进入 Workflow Definition。

### ADR-025：GUI 与 AI Authoring 必须操作同一套 Store / Validator / Registry

AI Workbench 的核心不是只让用户在 Vue Flow 里编辑，而是让 AI 也能通过 MCP Tools 管理同一批资产。

GUI 与 AI 必须共享：

```text
Asset Store
Validator
Registry / Catalog
Version / expected_version
Permission / Secret Policy
```

不得为 Vue GUI 和 AI Tool 分别实现两套保存规则。

目标 Authoring Tool 面：

```text
prompt_list / prompt_get / prompt_validate / prompt_save / prompt_delete
skill_list / skill_get / skill_validate / skill_save / skill_delete
mcp_connection_list / mcp_connection_get / mcp_connection_save
mcp_connection_delete / mcp_connection_test / mcp_connection_discover_tools
workflow_authoring_context / workflow_list / workflow_get
workflow_validate / workflow_save / workflow_delete
```

AI 创建或修改资产后，Desktop Catalog 必须立即看到变化；用户在 Desktop 修改后，AI 下一次 list/get 也必须看到最新版本，不要求重启 MCP Runtime。

### ADR-026：Workflow Node Kind 收敛为能力节点与流程控制节点

Node Role 与 Node Kind 继续保持分离：

```text
Node Role
input / process / output

Node Kind
AI Capability: skill / prompt
Action:        tool
Flow Control:  condition / approval / artifact
```

`prompt` 作为高级能力保留，但 UI 中弱化；普通用户优先使用 Skill。

`test` Node 在早期实现中与 `tool` 使用同一执行路径，因此不具备独立产品语义。0.3.x 最终模型按 ADR-032 将其从 schema、Runtime 与 UI 中完全移除；测试动作直接使用 Tool Node。只有未来 Test 获得独立的 assertion、report、coverage、retry 等真实语义时，才重新评估是否作为一等 Node Kind 引入。

### ADR-027：Prompt / Skill 是应用级全局能力资产，Workspace Target 只属于 Workflow

Prompt / Skill 的用途是被多个项目、多个 Workspace 和多个 Workflow 复用，因此它们不再以 Workspace 作为主要持久化作用域。管理页面不要求用户选择 Workspace Target。

新的职责边界固定为：

```text
Global Capability Assets
├── Prompt
├── Skill
└── MCP Connection

Workspace Assets
└── Workflow
    ├── target / workspace context
    └── references global capabilities
```

用户或 AI 新建、编辑 Prompt / Skill 时写入应用级 Global Store；每个 Workspace Runtime 自动加载 Built-in + Global Prompt / Skill。Workflow 在编辑、保存、运行时再选择 Workspace Target，并引用当前可见的 Prompt / Skill / Tool Catalog。

0.3.x 最终模型只读取 Built-in + Global Prompt / Skill，不读取 `<workspace>/.micromatrix-workbench/prompts|skills`。发布前内部测试数据不进入产品 migration pipeline，按 ADR-032 直接清理并重新初始化。

因此 Prompt / Skill 页面中的“Workspace Target”“Save Workspace Override”等概念全部移除，Built-in 资源第一次编辑后保存为 Global Override。

### ADR-028：MCP Connection 是 Global Tool Provider，Discovery 与 Workflow 执行分阶段接入

MCP Connection 与 Prompt / Skill 一样属于应用级 Global Capability Asset，不绑定单一 Workspace。第一版支持 `http` 与 `stdio` transport，并通过 Connection Test + `tools/list` Discovery 建立外部 Tool Catalog。

Connection 持久化只保存连接配置和 Secret Reference，不保存真实 Secret：

```text
environment        仅非敏感值
environment_refs   Secret Reference，例如 env:GITHUB_TOKEN
headers            仅非敏感值
header_refs        Secret Reference，例如 env:GITHUB_MCP_AUTH
```

发现后的 Tool 以稳定引用进入 Effective Tool Catalog：

```text
system:read_file
mcp:github:create_issue
```

Skill 从 Effective Tool Catalog 选择 Allowed Tools，并持久化 `ToolReference`，不复制外部 Tool 定义。禁用 MCP Connection 会让对应 Tool 从 Effective Tool Catalog 暂时消失，但不会自动删除 Skill 引用；删除 Connection 时若仍有 Skill 引用则必须阻止。

Phase 12 只负责 Connection、Secret Reference、Test、Discovery、Catalog 与 Skill Reference。Workflow Runtime 对外部 MCP Tool 的真实代理执行属于 Phase 13，因此 Phase 12 的 MCP Tool 标记为 `workflow_executable = false`，不得因为已经 discover 就误认为当前 Workflow 可以执行它。

### ADR-029：Workflow Tool Node 统一引用 ToolReference，外部 MCP Tool 通过受权限控制的 Proxy Adapter 执行

Phase 13 起，Workflow 不再区分“System Tool Node”和“MCP Node”。两者统一使用 `tool` Node Kind，只通过 `ToolReference` 区分能力来源：

```text
System Tool
provider = system
tool_name = read_file

MCP Tool
provider = mcp
connection_id = github
tool_name = create_issue
```

Workflow 只保存稳定引用和本节点 arguments，不复制 Tool description、input schema、Connection endpoint、Secret Ref 等能力定义。Effective Tool Catalog 是验证和编辑器选择的真相来源。

外部 MCP Tool 的执行固定为：

```text
Workflow Engine
      ↓
Runtime.call_tool(mcp_connection_call_tool)
      ↓
Permission Session / Permission Broker
      ↓
MCP Connection Service
      ↓
external tools/call
```

因此 Workflow 不获得额外网络或进程权限。HTTP MCP 仍需要 Network permission；stdio MCP 仍需要显式允许用户配置的外部可执行程序。Safe 模式不得因为 Workflow 引用而自动升级权限。

`mcp_connection_call_tool` 属于 Workbench control/execution adapter，不进入普通 Effective Tool Catalog，用户在 Workflow Editor 中选择的是实际外部 Tool Reference，而不是这个内部 adapter。

Node Library 同时收敛为：

```text
AI            Skill
Actions       Tool
Flow Control  Condition / Approval / Artifact
Advanced      Prompt
```

当前 Workflow Node Kind 只包含 `prompt / skill / tool / approval / condition / artifact`。`test` 不属于 0.3.x 正式模型；Tool Node 必须显式声明 `provider=system|mcp`，不再猜测旧裸 `tool_name` 的 Provider。

### ADR-030：Workflow Discovery Contract 使用一等 `description / inputs_schema / tags`

Phase 14 起，Workflow 不再只把 `name` 当作 AI discovery 入口。以下字段成为 Workflow Definition 的一等字段：

```text
description   说明这个 Workflow 什么时候应该被使用
inputs_schema 定义并校验 workflow_start.inputs
tags          提供轻量主题/用途标签
```

`workflow_list` 必须直接返回这三个字段，AI 不需要先 `workflow_get` 才能判断 Workflow 是否适合当前任务。`workflow_authoring_context` 同时返回 `workflow_contract`，明确 Authoring 所需字段和输入契约。

Workflow `description` 是 Definition 本身的必填领域字段。Authoring、Store 与 Runtime 使用同一合法性标准，不再维护“可运行但不可重新保存”的双重 Contract。

缺省 `inputs_schema` 可以使用开放 object schema；需要稳定调用契约的 Workflow 应声明明确 properties / required。`WorkflowRunManager.start` 会真实校验 inputs，而不是只把 schema 作为 UI 文档。

Vue Flow Editor 必须 round-trip 保存 `inputs_schema / tags / metadata`，AI 创建的 Workflow 通过 GUI 打开、调整和再次保存时不得丢失 discovery 元数据。

### ADR-031：Phase 15 冻结领域模型，Visual Editor 采用 Schema-driven 表单

Phase 15 不再增加新的 Capability Asset 或 Workflow Node Kind。产品化重点是让用户在 Vue Flow 中完成大多数编排工作，而不是继续扩大 Runtime 语义。

常见 Prompt / Skill / Tool 参数由已有 Prompt arguments 或 Tool `input_schema` 自动生成表单；Workflow `inputs_schema` 使用可视化 Object Schema Builder。复杂嵌套 Schema 始终保留 Advanced JSON，因此“降低手写 JSON 成本”不得以削弱表达能力为代价。

Catalog corruption recovery 使用 quarantine 而不是删除：当前版本无法解析的损坏 Prompt / Skill / Workflow / MCP Connection 移到相邻 `.quarantine`，保证 Catalog 继续启动并保留原始字节供诊断。检测到 future schema 时只跳过、不 quarantine，避免旧二进制修改由新版本产生的数据。

Workflow Editor 的 Undo / Redo 记录 Workflow Definition snapshot，而不是维护第二套 Graph 状态；节点搜索、能力搜索、节点级 Validation、Duplicate / Delete 等交互仍然只修改同一份 Definition。

### ADR-032：0.3.x 发布前不为内部试验格式保留兼容层

AI Workbench 0.3.x 尚未正式发布，因此开发阶段出现过的 Workspace Prompt / Skill、`allowed_tools: string[]`、`test` Node、裸 `tool_name` Tool Node、Global Workflow、缺失/0 `schema_version`、缺失 Workflow description 等格式都不构成用户兼容承诺。

最终 0.3.x Contract 直接收敛为：

```text
Prompt / Skill       Built-in + Global
Workflow             Built-in + Workspace
Skill tools          tool_references[]
Tool Node            provider + tool_name (+ connection_id for MCP)
Workflow Node Kind   prompt / skill / tool / approval / condition / artifact
schema_version       required and current
Workflow description required
```

删除内部试验兼容代码和迁移测试，测试机器上的旧数据通过人工清理重新初始化。正式发布后才为已经公开的数据格式建立版本迁移策略。

本 ADR 不影响真正的运行时稳定性和外部协议兼容：Catalog corruption quarantine、future schema protection、optimistic concurrency、Permission/Sandbox/Secret 规则，以及对第三方 MCP Server 协议版本的互操作能力继续保留。

### ADR-033：移除 Prompt 一等资产，Skill 成为完整 AI 执行单元且不配置 Tool Allowlist

0.3.x 发布前最终模型进一步收敛。此前把 Prompt 与 Skill 分成两层，并让 Skill 通过 `entry_prompt` 和 `tool_references` 组合执行能力，造成了重复 abstraction：Prompt 承担“给模型的指令”，Skill 的 `SKILL.md` 同样承担“给模型的方法和指令”；同时 Skill Tool Allowlist 又在 Runtime Permission/Sandbox 之外形成第二套能力边界。

最终决定：**Workbench 不再提供 Prompt 一等资产；Skill 自身就是完整的模型执行指令与方法文档。Skill 不声明 Tool 列表，执行 Skill 时默认可以使用当前 Runtime 可见的全部有效 Tool。**

最终职责边界固定为：

```text
Skill
  = 可复用 AI 方法 / 指令 / 执行规范
  = SKILL.md + 元数据 + Artifact 声明
  = 不引用 Prompt
  = 不保存 Tool allowlist

System Tool / MCP Tool
  = 原子执行能力
  = 是否真正可执行仍由 Runtime Permission、Sandbox、Connection 状态决定

Workflow
  = 编排 Skill / Tool / Approval / Condition / Artifact

Workflow Run
  = 保存真实执行状态、结果和审计历史
```

“Skill 默认使用全部 Tool”只取消 Skill 自己的二次 allowlist，不改变安全模型。最终有效权限仍然满足：

```text
Host / Runtime 当前可见 Tool
        ↓
Runtime Permission Profile
        ↓
Permission Broker / User Approval
        ↓
Sandbox / Workspace Boundary
        ↓
实际执行
```

因此 Skill 不能通过自身定义扩大 Runtime 权限，也不需要通过自身定义主动缩小 Tool 集合。若某个 Workflow 需要确定性调用某个 Tool，应使用显式 Tool Node，而不是把 Tool 白名单塞进 Skill。

0.3.x 最终 Contract 更新为：

```text
Global Capability Asset  Skill / MCP Connection
Workspace Asset          Workflow
Skill                    method_document + metadata + artifacts
Skill Prompt             不存在
Skill Tool Allowlist     不存在
Workflow Node Kind       skill / tool / approval / condition / artifact
Prompt Node              不存在
Tool Node                provider + tool_name (+ connection_id for MCP)
schema_version           required and current
```

相应删除：

- Prompt Registry / Store / Definition / authoring tools；
- Desktop Prompt 管理页面和 Prompt Catalog；
- Workflow Prompt Node；
- Skill `entry_prompt`；
- Skill `tool_references`；
- Skill 对 Prompt / Tool Reference 的保存期校验；
- Prompt/Skill 联动刷新、Prompt 删除依赖保护等耦合逻辑。

保留：

- `SKILL.md` 作为 Skill 的唯一模型指令来源；
- Skill Artifact 声明；
- Workflow Tool Node 的 `ToolReference`，因为它表达的是确定性执行目标，而不是 Skill 能力限制；
- MCP Connection 与 Effective Tool Catalog；
- Runtime Permission、Sandbox、Secret、Approval 等真实安全边界。

本 ADR 覆盖 ADR-007 中“Skill allowlist”、ADR-011 中 `Prompt -> Skill` 领域依赖、ADR-013 中 Prompt Node/Skill `entry_prompt` 组合语义、ADR-023/027 中 Prompt 作为 Capability Asset、ADR-026 中保留 Prompt Node、ADR-028 中 Skill Allowed Tools，以及 ADR-032 中包含 Prompt 和 Skill `tool_references[]` 的旧 0.3.x Contract。

由于 0.3.x 尚未发布，不建立旧 Prompt、`entry_prompt`、Skill `tool_references` 或 Prompt Node 的 migration/compatibility layer。开发测试数据直接清理并按最终 Contract 重新初始化。

---

## 3. 产品定位变化

当前定位：

```text
让 AI 安全操作本地代码
```

目标定位：

```text
让 AI 按可复用、可审计、可恢复的工程流程
安全操作本地代码
```

---

## 4. 不做的事情

第一阶段不做：

- Workflow Marketplace；
- 多 Agent 协同框架；
- 云端 Workflow 执行；
- 任意代码节点；
- 无上限循环；
- Skill 提升权限；
- Workflow 绕过 Permission Broker。

这些能力只有在基础模型稳定后再评估。


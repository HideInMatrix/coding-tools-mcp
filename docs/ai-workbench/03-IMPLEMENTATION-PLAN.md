# AI Workbench 实施计划

## 1. 原则

按可验证的垂直切片推进，不先做一个只有 UI 的 Workflow 画布。

每个阶段必须：

- 有数据模型；
- 有测试；
- 有明确完成标准；
- 保持现有 Tool / Permission / Sandbox Contract 不回归。

---

## Phase 0：决策与架构

状态：已完成

产物：

- `01-DECISIONS.md`
- `02-ARCHITECTURE-DESIGN.md`
- `03-IMPLEMENTATION-PLAN.md`
- `04-DOMAIN-MODEL.md`

完成标准：

- Prompt / Skill / Workflow / Run 边界明确；
- Vue Flow 只作为 Editor；
- AI 与 UI 共享 Workflow Schema；
- 权限边界确定。
- Prompt / Skill / Workflow / Run / Artifact / Approval 的生命周期和依赖关系确定。

门禁：上述四份文档未完成时，不继续扩展 Workbench 代码。

---

## Phase 1：Workbench Core + Prompt Registry

状态：已完成

目标：建立不依赖 UI 的核心 Registry，并补齐 MCP Prompts。

任务：

1. 新增 `agent_runtime/workbench/`；
2. 定义通用 Scope：Built-in / Global / Workspace；
3. 实现 PromptDefinition；
4. 实现 PromptRegistry；
5. 内置第一批 Prompt；
6. MCP initialize 声明 `prompts` capability；
7. 实现 `prompts/list`；
8. 实现 `prompts/get`；
9. 增加 Prompt Registry 单测与协议测试。

完成标准：

```text
MCP Client 可以发现 Prompt
MCP Client 可以按参数获取 Prompt
Workspace Prompt 可以覆盖 Built-in Prompt
```

---

## Phase 2：Skill Registry

状态：已完成

目标：建立可复用工程方法资源。

任务：

1. SkillDefinition；
2. `SKILL.md` loader；
3. Skill tool allowlist；
4. Built-in / Global / Workspace 合并；
5. `skill_list` / `skill_get`；
6. 内置：
   - `project-analysis`
   - `bug-investigation`
   - `reverse-engineering`
   - `code-review`
7. 单测。

完成标准：

- AI 能发现 Skill；
- Skill 可以引用 Prompt；
- Skill allowlist 只能收紧权限。

---

## Phase 3：Workflow Schema + Store + Validator

状态：已完成

目标：先让 Workflow 成为稳定的数据资源。

任务：

1. WorkflowDefinition / Node / Edge；
2. WorkflowStore；
3. version 机制；
4. DAG validation；
5. Prompt/Skill/Tool 引用 validation；
6. `workflow_list`；
7. `workflow_get`；
8. `workflow_validate`；
9. `workflow_save`；
10. `workflow_delete`；
11. 内置三套 Workflow Definition。

完成标准：

- AI 可以通过 MCP Tool 创建合法 Workflow；
- 非法环、悬空节点、未知 Tool 会被拒绝；
- 保存后可重新读取且版本稳定。

---

## Phase 4：Workflow Engine

状态：已完成

目标：建立与 UI 无关的 DAG 执行器。

任务：

1. Engine scheduler；
2. entry node / DAG traversal；
3. prompt node -> ModelAction；
4. skill node -> ModelAction；
5. tool node executor；
6. condition node；
7. Tool 调用接入 PermissionSession；
8. 节点结果标准化；
9. Engine 单元测试。

完成标准：

- Tool 权限仍经过 Permission Broker；
- Skill allowlist 在执行时真实生效；
- Engine 不依赖 Vue / DesktopAPI；
- Engine 不内置 LLM Provider，Prompt/Skill 产生可交给 MCP Client 的 ModelAction；
- 相同 Definition + 输入产生可预测的调度顺序。

---

## Phase 5：Workflow Run + Artifact + Approval

状态：已完成

目标：把一次真实执行变成可恢复、可审计的持久化实体。

任务：

1. WorkflowRun；
2. RunStore；
3. RunNodeState；
4. ArtifactRef / ArtifactStore；
5. ApprovalRequest；
6. approval node 暂停与继续；
7. resume / retry / cancel；
8. `workflow_start/status/continue/cancel`；
9. tool invocation summary；
10. 进程重启恢复测试。
11. Workflow Approval signed broker；
12. Desktop pending/respond approval API；
13. AI 不可伪造 approval 的安全测试；
14. Desktop/broker 重启后重新发布 pending approval。

完成标准：

- Run 不依赖前端存活；
- 等待审批后可以恢复；
- Artifact 有稳定引用；
- 程序重启后 Run 状态可读取；
- 失败节点可以按策略 stop/retry。
- Approval 决策必须来自 Desktop signed broker，不能由 Workspace 文件或 AI Tool 参数伪造。

---

## Phase 6：Vue Flow Workflow Editor

状态：已完成

目标：提供图形化工作流编辑页面。

依赖：

```text
@vue-flow/core 1.48.x
```

任务：

1. 增加 `AI 工作台` 一级菜单；
2. 增加 `/workbench` 路由；
3. Service / Workspace Profile Target selector；
4. Desktop Workbench CRUD / Validate Bridge；
5. Workflow 列表；
6. Vue Flow Canvas；
7. Node Library；
8. Inspector；
9. 自定义 Prompt/Skill/Tool/Approval/Condition/Artifact/Test Node；
10. Edge 创建与删除；
11. Save / Validate；
12. Run 状态映射到节点颜色；
13. Workflow Approval UI；
14. 保存布局 position；
15. 前端单测和 build。

完成标准：

- 用户可拖拽创建节点；
- 用户可连线调整执行顺序；
- Workflow 保存后重新打开布局一致；
- 保存前必须通过后端 Validator。

---

## Phase 7：AI 生成与修改 Workflow

状态：已完成

目标：自然语言和图形编辑器使用同一个 Workflow Definition。

任务：

1. `workflow_authoring_context` Tool；
2. Built-in `workflow-authoring` Prompt；
3. `workflow_save(expected_version)` optimistic concurrency；
4. Desktop/Vue Flow 使用同一 expected_version Contract；
5. AI 创建 Workflow；
6. AI 修改 Workflow；
7. 版本冲突测试；
8. 支持语言修改：
   - “在测试前加一个人工确认”；
   - “把安全审计放到代码修改之前”；
   - “删除 Release 节点”。

完成标准：

- AI 生成的 Workflow 与手动编辑完全互通；
- AI 保存后用户重新加载/选择 Workflow 即可在 Vue Flow 中看到同一 Definition；
- stale AI/UI save 不可覆盖较新版本；
- 未验证 Workflow 不可运行。

---

## Phase 8：默认项目 Workflow

状态：已完成

目标：让 Workbench 第一次打开时不是空编辑器，同时保持“Workflow 以用户自定义为主”的产品定位。

最终收敛为一个默认 Workflow：

1. Project Development。

默认工作流必须包含：

- Spec 驱动开发 Skill；
- Delivery Artifact；
- Human Approval；
- Acceptance metadata；
- 示例运行结果。

Bug Investigation、Reverse Engineering、Code Review、Release Validation 等保留为 Prompt / Skill，不再作为默认 Workflow。

---

## Phase 9：稳定性与迁移

状态：已完成

任务：

- Schema migration；
- Registry corruption recovery；
- Workflow import/export；
- Run history pruning；
- 大型 Graph 性能测试；
- Windows/macOS/Linux 路径测试；
- Safe Sandbox 权限回归；
- 文档与用户指南。

完成结果：

- Prompt / Skill / Workflow / Run schema 支持缺失版本或 v0 迁移到 v1；
- 高于当前版本的 schema 明确拒绝，不进行猜测式迁移；
- 单个损坏 Prompt / Skill / Workflow / Run 不再阻断整个 Registry/Runtime 启动；
- Workflow 支持 JSON import/export；
- Run history 支持安全 pruning，非终态和失败 Run 不被自动清理；
- Workflow DAG 环检测改为迭代式拓扑检测，5,000 节点长链通过稳定性测试；
- Workspace 空格、Unicode 与资源 ID 路径边界通过测试；
- Workflow Tool Node 继续复用 Runtime Permission/Sandbox，不建立绕过路径；
- Workbench / Desktop / Gateway 关键综合回归 119 tests 通过；
- Phase 9 stability 13 tests 通过；
- 前端 3 tests、TypeScript、Vue TypeScript 与 Vite production build 通过；
- 项目级旧 Contract 中 5 个写死 Tool 数量的断言已改为 Registry 一致性 Contract。

当前执行环境中的 4 个既有 Error 记录在 `07-ACCEPTANCE.md`，不通过放宽 Safe Sandbox 规避。

---

## Phase 10：Capability Asset Domain

状态：已完成

目标：冻结 Prompt / Skill / MCP Connection / Workflow / System Tool 的职责与共享 Domain Service，先解决领域模型，不先改 Vue Flow 外观。

任务：

- Prompt / Skill Store、Registry、Validator、version 与 optimistic concurrency 统一进入共享 Domain Service；
- 定义统一 Tool Reference：`system` / `mcp`；
- Skill 使用稳定 `tool_references[]`；Workflow Tool Node 使用显式 `provider + tool_name`；
- GUI 与 MCP Authoring Tools 共享同一 Domain Service。

完成标准：

- Prompt / Skill 能通过 API 创建、更新、删除并即时刷新 Global Registry；
- expected_version 冲突测试通过；
- UI/AI 不存在两套保存逻辑。

完成结果：

- 新增 `CapabilityAssetService`，Desktop 与 MCP Runtime 共用 Prompt / Skill Store、Validator、Registry 刷新与 optimistic concurrency 语义；
- Prompt / Skill 最终作用域收敛为 `Built-in + Global`；用户与 AI 的新增/修改只进入应用级 Global Store；
- Skill 保持 `skill.json + SKILL.md` 资产格式，但工具授权只持久化稳定 `tool_references[]`；
- Workflow Tool Node 必须显式使用 `provider=system|mcp`；`test` 不属于 0.3.x 最终 Node Kind；
- Phase 10 只建立 MCP Tool Reference 结构，外部 MCP Tool 在 Phase 12 接入前由 Validator 明确标记为 unavailable，不会误调用本地同名 Tool；
- Desktop Bridge 已具备 Prompt / Skill validate/get/save/delete 后端 API，为 Phase 11 UI 直接复用同一 Domain Service 提供接口基础；
- Prompt 被 Global Skill 引用且不存在 Built-in fallback 时禁止删除，避免产生悬空资产；
- `tests/test_workbench_capability_assets.py` 覆盖 version conflict、Global override 回退、Tool Reference、显式 Provider 与依赖删除保护；
- Phase 10 门禁回归：Workbench / Run / Approval / Stability / Desktop 共 89 tests 通过。

---

## Phase 11：Prompt / Skill Management UI + AI Authoring

状态：已完成

目标：让用户与 AI 都可以真正维护 Prompt 和 Skill，而不只是查看 Built-in Registry。

补充边界：Prompt / Skill 按 ADR-027 调整为应用级 Global Capability Asset，管理页面不再选择 Workspace Target；Workspace Target 只在 Workflow 编排/运行时指定。

任务：

- Workbench 增加 Prompts 管理页；
- Workbench 增加 Skills 管理页；
- Prompt / Skill 管理页移除 Workspace Target；
- Prompt / Skill 新写入 Global Store，Built-in 第一次编辑保存为 Global Override；
- 每个 Workspace Runtime 自动加载 Global Prompt / Skill；
- Skill 可选择 entry Prompt；
- Skill 可从 Effective Tool Catalog 选择 Allowed Tools；
- 增加 `prompt_validate/save/delete`；
- 增加 `skill_validate/save/delete`；
- AI 修改后 Desktop Catalog 实时更新；
- Desktop 修改后 MCP Runtime list/get 实时更新。

完成标准：

- 用户可完整 CRUD Global Prompt / Skill；
- AI 可通过 MCP Tools 完成同样 CRUD；
- AI 与 GUI 创建的资源格式完全一致；
- Built-in Prompt / Skill 可读取，但不得被误删；Global Override 继续使用 expected_version 规则。

当前实现结果：

- 新增 `prompt_list/get/validate/save/delete` 与 `skill_validate/save/delete` MCP Authoring Tools；已有 `skill_list/get` 继续复用；
- AI Authoring 与 Desktop API 全部调用同一 `CapabilityAssetService`，不存在第二套 Prompt / Skill 保存规则；
- MCP Runtime 在 native Prompt list/get、Skill list/get、Workflow validate/save/start 和 authoring context 前刷新 Capability Asset Registry，Desktop 改动无需重启 Runtime 即可看到；
- Desktop Catalog 每次读取直接从共享 Store / Registry 重建，AI 保存后 GUI 下一次刷新即可看到同一份资产；
- 新增 Prompts 管理页：不绑定 Workspace Target，支持 Built-in/Global 查看、新建、验证、Global Override、version save、删除；
- 新增 Skills 管理页：不绑定 Workspace Target，支持 entry Prompt、Method/Instructions、Artifacts、Allowed System Tools、Built-in Override 与 Global CRUD；
- Prompt / Skill Global Store 位于应用级 Workbench 数据目录；不同 Workspace Runtime 自动加载同一份 Global Capability Catalog；
- Desktop 提供独立 Capability Catalog，Prompt / Skill 页面不再依赖 `target_id`；Workflow Editor 继续通过 Target Catalog 获取 Global Capability 与 Workspace Workflow；
- 新增跨 Workspace Contract：Workspace A 创建的 Global Prompt / Skill，Workspace B Runtime 无需复制即可 list/get 并引用；
- Sidebar 增加 AI 工作台下的 `Prompts` / `Skills` 子入口；
- Workbench 控制面 Tool（`workflow_*` / `prompt_*` / `skill_*` / `mcp_connection_*`）从普通 Skill / Workflow 可执行 Tool Catalog 排除，避免把 Authoring/Run 控制工具重新编排进业务流程；
- 新增 AI Authoring、跨进程刷新与跨 Workspace Global Capability Contract；Phase 10–11 相关后端综合回归共 92 tests 通过；
- Python `compileall` 已通过。

验收说明：

- Phase 10–11 后端 / Workbench / Desktop 综合回归共 92 tests 通过，Python `compileall` 通过；
- 用户已用最新源码启动桌面前端并实际进入 Prompt / Skill 管理页面完成 UI smoke，页面无运行时错误；
- 2026-08-20 Node / pnpm 工具链已恢复可执行；修复 `WorkbenchCatalogDto` 新增 `effective_tools` / `mcp_connections` 后空 Catalog 未同步的问题；
- 已实际执行 `pnpm run build`，`tsc --noEmit + vue-tsc --noEmit + vite build` 全部通过。

---

## Phase 12：MCP Connection Management + Tool Discovery

状态：已完成

目标：让用户 / AI 管理外部 MCP 能力来源，并把 discover 到的 MCP Tools 合并到 Tool Catalog。

任务：

- 建立 MCP Connection schema/store/validator；
- 支持 create/update/delete/enable/disable；
- 支持 connection test；
- 支持 tool discovery；
- MCP Secret 只允许 `*_ref` / Secret Binding，不保存明文；
- 建立 Effective Tool Catalog；
- 增加对应 AI Authoring Tools。

完成标准：

- 一个外部 MCP Connection 可以被添加并完成 discovery；
- discovered Tool 带 description 与 input schema；
- Connection 更新/删除后 Tool Catalog 立即同步；
- Secret 明文测试明确拒绝；
- 外部 MCP Tool 仍受本地授权边界约束。

当前实现结果：

- 新增 Global `MCPConnectionDefinition / MCPConnectionStore / MCPConnectionService`，支持 `http` / `stdio`、version、enable/disable 与 optimistic concurrency；
- Global Store 使用 `workbench/mcp-connections/*.json`，与 Prompt / Skill 一样不绑定 Workspace Target；
- Secret 明文保护覆盖敏感 Header / Environment Key，第一阶段 Secret Reference 支持 `env:NAME`；
- HTTP MCP Client 支持 2026-07-28 stateless `server/discover + tools/list`，并兼容 legacy `initialize + tools/list`；
- stdio MCP Client 使用 direct argv（不经过 shell）并使用后台 reader + 有界 timeout，避免坏掉的外部 MCP 无限阻塞；
- Discovery 持久化 Tool `name / description / input_schema`、发现时间与最后错误；
- 新增 Effective Tool Catalog：`System Tools + enabled discovered MCP Tools`，稳定 key 为 `system:<tool>` / `mcp:<connection>:<tool>`；
- Skill 管理页的 Allowed Tools 已切换到 Effective Tool Catalog，可同时保存 System / MCP `ToolReference`；
- 禁用 Connection 时对应 MCP Tool 从 Effective Catalog 消失但 Skill 引用保留；仍被 Skill 引用的 Connection 禁止删除；
- 新增 AI Authoring Tools：`mcp_connection_list/get/validate/save/delete/test/discover_tools`；HTTP Test/Discovery 进入 Network permission 边界，stdio 进入外部进程授权边界；
- Desktop 新增 `MCP 服务` 全局管理页，支持 CRUD、enable/disable、Test、Discovery、Secret Refs 和 discovered Tool 浏览；
- `workflow_authoring_context` 已返回 MCP Connection + Effective Tool Catalog，但 Phase 12 MCP Tool 明确标记 `workflow_executable=false`；真实 Workflow proxy execution 保留到 Phase 13；
- Contract 测试真实启动本地 MCP HTTP Server 与 stdio 子进程 MCP fixture 完成协议协商与 Tool Discovery，不只使用 mock；
- Phase 10–12 Workbench / Desktop 综合回归共 97 tests 通过，Python `compileall` 通过；stdio 测试同时以 `ResourceWarning` 视为 error 验证子进程句柄完整释放。

验收结果：

- 用户已完成桌面应用打包与运行验证，`AI 工作台 -> MCP 服务` 实机运行正常；
- Phase 12 前端已实际执行 `pnpm run build`，`tsc --noEmit + vue-tsc --noEmit + vite build` 全部通过；
- Phase 12 作为 Connection / Discovery / Effective Tool Catalog 阶段正式 PASS，Workflow MCP Tool proxy execution 进入 Phase 13。

---

## Phase 13：Workflow Editor Capability Refactor

状态：已完成

目标：让 Vue Flow 只负责“引用并编排能力资产”。

任务：

- Node Library 改为 AI / Actions / Flow Control 三组；
- Skill 为主要 AI Node；
- Prompt 移入 Advanced；
- Workflow Node Kind 收敛为 `prompt / skill / tool / approval / condition / artifact`；
- Tool Node 增加 Provider / Connection / Tool 选择；
- Skill / Prompt Node 改为 Catalog 引用；
- Node Role 继续独立保持 input / process / output；
- 保留已有节点边框颜色、Handle 和 Resize/FitView 行为。

完成标准：

- Workflow 不嵌入 Prompt / Skill / MCP Connection 完整定义；
- Catalog 资源删除或不可用时 Validator 能指出具体引用错误；
- System Tool 与 MCP Tool 都能进入 Workflow；
- Tool Node 必须保存稳定且显式的 Provider 引用。

当前实现结果：

- Node Library 已按 `AI / Actions / Flow Control / Advanced` 分组；Skill 是主要 AI Node，Prompt 进入 Advanced；
- `test` 已从 Workflow Node Kind、Runtime、Canvas 与 Inspector 中移除；测试动作统一通过 Tool Node 表达；
- Tool Node 使用稳定 `ToolReference`：`provider=system + tool_name` 或 `provider=mcp + connection_id + tool_name`；
- Tool Inspector 已实现 `Provider -> Connection -> Tool` 动态选择，并读取 Target 的 Effective Tool Catalog；
- Tool Node 缺少 `provider` 直接视为无效 Definition，不再猜测为 System Tool；
- Workflow Validator 使用 Effective Tool key 校验，MCP Connection 禁用、删除或 Tool 未发现时返回 `unknown_tool`；
- Workflow Engine 已通过内部 `mcp_connection_call_tool` adapter 代理执行外部 MCP Tool，同时仍走 `Runtime.call_tool -> Permission Session / Broker`；
- HTTP MCP Tool 调用先以无副作用 `server/discover` 判定 modern/legacy 协议，再执行一次 `tools/call`，避免失败 fallback 导致有副作用 Tool 重复调用；
- stdio MCP Tool 调用保持 `initialize -> notifications/initialized -> tools/call`，继续使用 bounded timeout 与完整进程句柄回收；
- Safe 模式外部 HTTP MCP Tool 调用验证为 `PERMISSION_REQUIRED`，不会因为 Workflow 编排绕过 Network permission；
- Skill ModelAction 新增 `allowed_tool_references`，MCP Tool Reference 不再在 Skill 执行阶段丢失；含 MCP Tool 的 Skill 同时暴露内部 adapter `mcp_connection_call_tool` 给宿主执行；
- `workflow_authoring_context` 只暴露最终 0.3.x Node Kind，并把 MCP Tool 标记为 Workflow 可执行；
- 前端新增 System/MCP Tool Reference round-trip 测试，确认 Workflow 不嵌入 MCP Connection / Tool 完整定义；
- 前端 `pnpm test` 4 tests PASS；`pnpm run build` 的 `tsc + vue-tsc + vite build` 全部通过；
- Phase 10–13 Workbench / Desktop 综合后端回归共 103 tests PASS；MCP HTTP/stdio 执行、安全边界和 ResourceWarning Contract 均通过；Python `compileall` PASS。

验收结果：

- 用户确认打包与运行正常，并继续进入 Phase 14；
- 自动门禁已包含 103 个 Workbench / Desktop 后端回归、Python `compileall`、前端 4 tests 与 production build；
- Phase 13 作为 Capability Refactor 基线冻结，后续 discovery / authoring 只在其上增加元数据契约，不重新改变 Node Kind / Tool Provider 模型。

---

## Phase 14：Workflow Discovery + AI End-to-End Authoring

状态：已完成

目标：让 AI 不仅能运行 Workflow，还能通过自然语言创建能力并完成编排。

任务：

- Workflow 增加 `inputs_schema`；
- Workspace Workflow 要求有有效 description；
- 可选增加 tags；
- `workflow_list` 返回 discovery 所需摘要；
- `workflow_authoring_context` 返回 Prompt / Skill / Effective Tool / MCP Connection Catalog；
- 验证 AI 链路：创建 Prompt -> 创建 Skill -> 创建 Workflow -> validate -> save -> start；
- 验证 AI 修改已有 Workflow 的 optimistic concurrency。

完成标准：

- AI 能根据 `workflow_list` 判断 Workflow 用途和输入；
- AI 能从零创建一套 Prompt + Skill + Workflow；
- 用户无需打开 GUI 也能用自然语言完成 Authoring；
- 打开 GUI 后能看到 AI 创建的同一份资产和 Graph。

当前实现结果：

- `WorkflowDefinition` 新增一等 `inputs_schema` 与 `tags`；`inputs_schema` 未显式声明时可使用开放 object schema，tags 缺省为空；
- `workflow_list` summary 直接返回 `description / inputs_schema / tags / version / scope / node_count / edge_count`，AI 无需逐个 `workflow_get` 即可完成 discovery；
- `workflow_authoring_context` 新增 `workflow_contract`，并继续返回 Prompt / Skill / Effective Tool / MCP Connection / Workflow Catalog；
- Workflow description 在 `WorkflowDefinition` 领域入口强制必填，AI/UI Authoring、Store 与 Runtime 使用同一合法性标准；
- `WorkflowRunManager.start` 在创建 Run 前真实使用 `inputs_schema` 校验 `workflow_start.inputs`；缺少 required 或违反 additionalProperties 等约束会拒绝启动；
- 默认 `Project Development` Workflow 增加 `feature` input schema 与 `project/development/spec` tags；
- Vue Flow Editor round-trip 保留 `inputs_schema / tags / metadata`；点击画布空白区域后可在 Workflow Inspector 编辑 description、tags 和 Inputs Schema；
- 新增 AI 端到端 Contract：`prompt_save -> skill_save -> workflow_validate -> workflow_save -> workflow_list -> workflow_start` 全链路通过；
- 同一 Contract 验证 missing required input 被拒绝，以及 stale `expected_version` 返回 `WORKFLOW_VERSION_CONFLICT` 后用当前版本更新成功；
- 新增 AI -> Desktop 一致性 Contract：运行中的 AI Runtime 保存 Workflow 后，Desktop Catalog/Graph 读取到同一份 description、inputs_schema、tags、metadata 与节点引用；
- 前端 Workflow graph test 验证 discovery metadata 与 Tool Reference 同时 round-trip，不会因 GUI 再保存而丢失 AI 创建的数据；
- Phase 10–14 Workbench / Desktop 综合后端回归共 106 tests PASS；Python `compileall` PASS；前端 `pnpm test` 4 tests PASS，`tsc + vue-tsc + vite build` PASS。

验收结果：

- AI 可以从 `workflow_list` 直接判断 Workflow 用途与输入；
- AI 可以在不打开 GUI 的情况下创建 Prompt + Skill + Workflow 并启动 Run；
- GUI 与 AI 共用相同 WorkflowStore / Definition，AI 创建后的 Graph 与 discovery metadata 可由 Desktop 直接读取和继续编辑；
- Phase 14 Contract 已全部自动化通过，进入 Phase 15 前不再修改 discovery 字段语义。

---

## Phase 15：Visual Workflow Editor Productization + Version Boundary + Final Acceptance

状态：已完成

目标：在 Phase 10–14 已冻结的能力模型上，把 Vue Flow Workflow Editor 从“可编辑”打磨到“可正式交付给用户”，同时冻结 0.3.x 数据边界、完成故障恢复、性能和最终验收。Phase 15 不再改变 Prompt / Skill / MCP / ToolReference / Workflow 的核心领域关系。

任务：

- Workflow Inspector 根据 Prompt / Tool 的 input schema 生成常用参数表单，并保留 Advanced JSON；
- Workflow `inputs_schema` 提供可视化参数 Builder，并保留 Advanced JSON Schema；
- Node Library 增加搜索 / 分类筛选；
- Canvas 增加 Undo / Redo、Duplicate Node、删除确认和更明确的节点级 Validation 反馈；
- 引用失效、MCP Connection 禁用、参数 Schema 错误在节点 / Inspector 就地提示；
- 空状态、搜索、筛选和大 Catalog 交互优化；
- 当前 `schema_version` 严格校验与 future schema protection；
- 删除 0.3.x 发布前内部试验格式的兼容分支；
- MCP Connection 故障恢复；
- Catalog corruption recovery；
- AI / Desktop 跨进程一致性测试；
- 大型 Catalog 与 Graph 性能测试；
- User Guide / Acceptance 更新；

完成标准：

- Phase 10–14 的 Contract 全部通过；
- AI 创建的 Workflow 打开 Vue Flow 后可完整查看、配置、验证并保存，`description / inputs_schema / tags / metadata / capability refs` 不丢失；
- 常见 Workflow / Prompt / Tool 参数无需手写 JSON 即可完成配置，Advanced JSON 仍可用于复杂 Schema；
- 节点级错误能够在 Canvas / Inspector 直接定位，不只显示“有 N 个错误”；
- Node Library 在大 Catalog 下可以搜索并保持可用；
- 0.3.x 最终 Definition 不包含内部试验时期的 Workspace Prompt/Skill、Test Node、裸 Tool Provider 等兼容入口；
- 不以降低 Sandbox / Permission / Secret 安全规则换取便利性。

实际完成：

- Workflow Inspector 新增 Schema-driven Arguments Editor：Prompt 参数、Skill Entry Prompt 参数、System/MCP Tool `input_schema` 均可生成常用表单；复杂 object/array 和特殊场景保留 Advanced JSON；
- Workflow `inputs_schema` 新增可视化 Object Schema Builder，可添加/删除/重命名参数、设置类型/说明/Required/Additional Properties，并保留 Advanced JSON Schema；
- Node Library 新增节点类型搜索；Prompt / Skill / Tool Inspector 新增能力搜索，适配大 Catalog；
- 编辑器新增 Undo / Redo（最多保留 100 个 Definition snapshot）、Duplicate Node、节点/Edge 删除确认和 Workflow 删除确认；
- Validator 的 `subject` 已映射到 Canvas Node / Inspector：节点错误显示数量徽标和错误 ring，Inspector 就地显示具体错误；Prompt / Skill / MCP Tool 引用失效也有直接提示；
- 空 Workflow Canvas 新增空状态入口，可直接打开 Node Library；
- 根据 ADR-032 删除发布前内部试验兼容：Prompt/Skill 只允许 Built-in + Global，Workflow 只允许 Built-in + Workspace；Skill 只持久化 `tool_references[]`；Workflow 无 Test Node；Tool Node 必须显式 Provider；
- `schema_version` 必须显式为当前版本；缺失、0 或其他旧试验版本直接拒绝。Workflow description 在领域模型层必填，不存在 Runtime/Authoring 双重标准；
- Prompt / Skill / Workflow / MCP Connection Catalog 损坏条目会自动移动到相邻 `.quarantine` 目录，Catalog 继续加载；future schema 数据只跳过不 quarantine，避免旧版本修改新版本数据；
- MCP rediscovery 失败时保留最后一次成功的 Tool Catalog 并记录 `last_error`，后续可直接重试；同时修复 HTTPError response handle 资源释放，`ResourceWarning-as-error` Contract 通过；
- Phase 14 的 description / inputs_schema / tags 与 AI/Desktop round-trip Contract 保持；
- 大图性能门禁继续验证 5,000-node DAG；新增 2,000 Workflow Catalog list + summary 性能门禁；
- 发布前兼容性测试已删除并替换为最终 0.3.x Contract 测试；Phase 10–15 Workbench / Desktop / MCP / Approval 综合回归最终为 105 tests PASS；
- MCP Connection 以 `ResourceWarning` 视为 error 的资源释放门禁 4 tests PASS；Tool Framework 8 tests PASS；Python `compileall` PASS；
- 前端 `pnpm test` 4 tests PASS；`tsc --noEmit + vue-tsc --noEmit + vite build` production build PASS；
- 项目级 Python 全量门禁在隔离 Git 全局配置后为 323 tests PASS，3 skipped；其中当前 Safe Seatbelt 不提供 POSIX PTY 时 TTY Contract 明确 skip，未通过放宽 Sandbox 规避。

---

## 2. 强制执行顺序

从本次任务开始严格按下列门禁推进：

```text
Phase 0
  ↓
Phase 1  Prompt Registry + MCP Prompts
  ↓
Phase 2  Skill Registry
  ↓
Phase 3  Workflow Definition / Store / Validator
  ↓
Phase 4  Workflow Engine
  ↓
Phase 5  Workflow Run / Artifact / Approval
  ↓
Phase 6  Vue Flow Editor
  ↓
Phase 7  AI Natural Language Authoring
  ↓
Phase 8  Default Project Workflow
  ↓
Phase 9  Stability / Migration / Acceptance
  ↓
Phase 10 Capability Asset Domain
  ↓
Phase 11 Prompt / Skill Management + AI Authoring
  ↓
Phase 12 MCP Connection + Tool Discovery
  ↓
Phase 13 Workflow Capability Refactor
  ↓
Phase 14 Workflow Discovery + End-to-End AI Authoring
  ↓
Phase 15 Visual Workflow Editor Productization + Version Boundary + Final Acceptance
```

规则：

- 一个 Phase 的完成标准未通过，不进入下一 Phase；
- 已提前存在的后续 Phase 代码只视为预研骨架，冻结，不继续扩展；
- 如果模型发生变化，先改文档再改代码；
- 每个 Phase 结束都要给出可测试点和回归结果；
- 不因为 Vue Flow 页面可见性高而跳过 Skill / Engine / Run。

### 当前代码状态说明

在本计划冻结前已经出现少量 Phase 3 / Phase 6 的预研代码（Workflow Schema/CRUD 骨架与 Vue Flow 页面骨架）。这些代码保留用于后续复用，但不代表对应 Phase 已完成。从现在开始先完成并验收 Phase 1，再进入 Phase 2。

---

## 3. 每阶段测试点

### Backend

```text
python -m unittest ...
python -m compileall ...
```

### Frontend

```text
npm run build
```

Release CI 继续统一使用 npm；本地开发允许使用 pnpm。

---

## 4. Git 规则

本计划不会改变项目现有 Git 权限约束。

只有用户明确要求“提交”时才执行 Git 写操作。


# AI Workbench 最终验收记录

## 1. 验收范围

本次验收覆盖 `03-IMPLEMENTATION-PLAN.md` 中 Phase 0 到 Phase 9。

目标能力：

```text
Prompt
Skill
Workflow
Workflow Run
Vue Flow Editor
AI Natural Language Authoring
```

同时保持 Permission Broker、Safe Sandbox、Gateway、OAuth 和原有 Tool Framework 的边界。

---

## 2. Phase 状态

```text
Phase 0  决策 / 架构 / 领域模型              PASS
Phase 1  Prompt Registry + MCP Prompts       PASS
Phase 2  Skill Registry                      PASS
Phase 3  Workflow Schema / Store / Validator PASS
Phase 4  Workflow Engine                     PASS
Phase 5  Run / Artifact / Signed Approval    PASS
Phase 6  Vue Flow Editor                     PASS
Phase 7  AI Authoring + Version Conflict     PASS
Phase 8  Default Project Workflow            PASS
Phase 9  Stability / Migration               PASS
```

---

## 3. 后端关键验收

综合关键回归：

```text
119 tests
OK
```

范围包括：

- Workbench Prompt / Skill / Workflow / Engine / Run；
- Signed Workflow Approval Broker；
- DesktopAPI；
- Tool Framework；
- Gateway Framework / Launcher / Profiles。

Phase 9 stability：

```text
13 tests
OK
```

覆盖 schema migration、损坏资源隔离、Workflow import/export、Run pruning、Unicode/空格 Workspace、路径边界、Safe 权限回归和 5,000 Node DAG。

Python 编译：

```text
python -m compileall -q mcp_tools_server coding_tools_launcher
OK
```

---

## 4. 前端验收

```text
pnpm test
3 tests PASS
```

覆盖：

- clean install / starting-state regression；
- Workflow Definition ↔ Vue Flow Canvas round-trip。

Production build：

```text
tsc --noEmit
vue-tsc --noEmit
vite build
PASS
```

Vue Flow Workbench 作为 lazy chunk 构建。

Release / CI 继续使用 npm。本地验证使用 pnpm 不改变 Release 包管理器约定。

---

## 5. 项目级 discover

执行：

```text
python -m unittest discover -s tests
```

本次执行共：

```text
303 tests
```

Workbench 扩展后最初出现 5 个 Assertion Failure，原因是旧测试将 MCP Tool 数量写死为 `20`。

这些测试已经改为验证：

```text
Runtime Tool Registry
        ==
Protocol / HTTP tools/list
```

不再使用历史魔法数字。

修正后单独重跑 `tests.test_custom_mcp_server`，没有新的 Assertion Failure。

---

## 6. 当前 Safe 执行环境的测试归一化

项目级全量门禁最初暴露出 4 个环境型 Error。没有通过降低 Sandbox 或产品安全策略规避，而是把测试夹具改成与产品真实运行契约一致。

### 6.1 certifi optional dependency fixture

```text
test_cimd_https_connection_loads_certifi_ca_bundle
```

当前环境中：

```text
mcp_tools_server.server.certifi is None
```

源码最小环境允许 `certifi` 不安装，而桌面发布依赖通过 `requirements-desktop.txt` 提供。测试不再依赖当前解释器实际安装 certifi，而是注入 fake certifi 对象，仍然真实验证“存在 certifi 时加载 CA bundle”的产品行为。

### 6.2 Git fixture × 2

```text
test_git_read_tools_keep_project_contract_fields
test_git_status_reports_workspace_changes
```

Safe Sandbox 环境中的 macOS Git/Xcode 会尝试访问：

```text
/var/.../xcrun_db-*
~/.gitconfig
```

项目级门禁运行时使用 `GIT_CONFIG_GLOBAL=/dev/null` 隔离用户全局 Git 配置，不放宽 Workspace / `.git` / HOME 沙箱边界。两个 Git Contract 均真实执行并通过；macOS `xcrun_db` 仍可能打印不可写 warning，但不影响 Git 测试结果。

### 6.3 TTY fixture

```text
test_tty_command_accepts_follow_up_stdin
```

当前 Safe Seatbelt 无法创建 POSIX pseudo-terminal 时，Runtime 会明确返回 `TTY_UNSUPPORTED`。TTY 测试在检测到该环境能力缺失时 skip；如果 PTY 可用，则继续验证 `command_id -> write_stdin -> exited` 完整链路。

---

## 7. 安全验收

确认以下 Contract 成立：

- Skill allowlist 只能收紧 Tool 能力；
- Workflow Tool Node 禁止调用 `workflow_*` 控制 Tool；
- Workflow 不保存 Password / Token / Secret / API Key 明文；
- Condition 不执行 Python / JavaScript / eval；
- Artifact 不接受任意 Workspace 输出路径；
- Workflow 内部 Tool 调用继承原 MCP RequestContext / principal；
- Tool 调用仍经过 Permission Session / Permission Broker / Sandbox；
- Human Approval 使用 Desktop-owned signed IPC；
- AI 不能通过 `workflow_continue` 参数批准人工 Approval；
- stale AI/UI save 通过 `expected_version` 拒绝；
- Run 保存不可变 Workflow snapshot。

---

## 8. 稳定性验收

确认：

- 当前 Definition 必须显式携带当前 schema_version；缺失、v0 和其他发布前试验版本直接拒绝；
- 未知未来 schema 明确拒绝；
- 单个坏 Prompt / Skill / Workflow / Run 不拖垮 Registry；
- 损坏用户文件不会被自动删除；
- Run history 支持 pruning；
- 非终态和 failed Run 不会被后台 pruning 误删；
- Workflow import/export 使用同一 Schema；
- 5,000 节点线性 DAG 使用迭代拓扑算法验证，不依赖 Python recursion depth；
- Workflow ID / Run ID / Artifact ID 有路径逃逸边界测试。

---

## 9. 第一版明确不做

```text
内置 LLM Provider
多 Agent 编排
Workflow Marketplace
云端 Workflow Runtime
任意 Python / JavaScript Code Node
隐式循环
普通 Edge 的 AND Join
```

Prompt / Skill 的 ModelAction 由当前 MCP AI Client 处理，这是架构决策，不是遗漏实现。

---

## 10. 下一阶段候选

如果继续扩展，应重新写下一阶段决策与计划，不直接塞回 Phase 0–9：

```text
显式 Join Node
显式 Loop Node
Desktop Workflow Start UX
Workflow Import/Export 图形化入口
Prompt / Skill 管理 UI
Run Timeline / Artifact Preview
Workflow Template Marketplace
可选内置 AI Provider Adapter
```

后续能力不得绕过当前已冻结的 Schema、Permission、Approval 和 Version Conflict Contract。

---

## 11. Capability Refactor / Productization 最终验收（Phase 10–15）

Phase 0–9 的历史验收记录保持不变。后续根据 ADR-023～032 对 AI Workbench 进行了 Capability Asset 重构、Visual Editor 产品化与 0.3.x 数据边界收敛，最终状态如下：

```text
Phase 10  Capability Asset Domain                 PASS
Phase 11  Prompt / Skill Management + AI Author  PASS
Phase 12  MCP Connection + Tool Discovery        PASS
Phase 13  Workflow Capability Refactor            PASS
Phase 14  Workflow Discovery + AI Authoring       PASS
Phase 15  Visual Editor + Version Boundary        PASS
```

Phase 10–15 已确认：

- Prompt / Skill 是应用级 Global Capability Asset，管理页面不再绑定 Workspace Target；
- Workflow 仍属于 Workspace Target，并通过稳定 ID 引用 Prompt / Skill；
- MCP Connection 是 Global Tool Provider，不是新的 Workflow Node Kind；
- MCP Connection 支持 HTTP / stdio、optimistic concurrency、enable/disable、Secret Reference、Connection Test 与 Tool Discovery；
- HTTP Contract 测试真实启动项目自己的 MCP HTTP Server 完成协议协商与 `tools/list`；
- stdio Contract 测试真实启动子进程 MCP fixture，并使用有界 timeout 验证 discovery；
- Effective Tool Catalog 聚合 System Tool 与已启用 MCP Connection 的 discovered Tool；
- Skill 可持久化 System / MCP `ToolReference`；Connection 禁用不删除 Skill 引用，Connection 被 Skill 引用时禁止删除；
- AI 与 Desktop 使用同一套 MCP Connection Store / Validator / Discovery Service；
- 用户已完成 Phase 12 桌面打包与运行 smoke，MCP 服务管理页面运行正常；
- Phase 13 Node Library 已收敛为 `AI / Actions / Flow Control / Advanced`，Workflow Node Kind 固定为 `prompt / skill / tool / approval / condition / artifact`；
- Workflow Tool Node 统一使用 System / MCP `ToolReference`，Tool Node 必须显式保存 provider，不再接受裸 `tool_name` 推断；
- MCP Tool 已通过 `mcp_connection_call_tool` proxy adapter 接入 Workflow Engine，仍经过 Runtime Permission Session / Broker；
- Safe 模式 HTTP MCP Workflow Tool 调用返回 `PERMISSION_REQUIRED`，证明 Workflow 不会绕过 Network permission；
- HTTP MCP Tool Call 在无副作用 protocol detection 后只执行一次 `tools/call`，防止 fallback 导致有副作用 Tool 重复执行；
- stdio Tool Call 使用 initialize/initialized/tools-call 顺序并保持 bounded timeout / ResourceWarning-as-error 契约；
- MCP Connection 禁用后 Workflow Validator 返回 `unknown_tool`，重新启用后可恢复；
- Skill ModelAction 携带 `allowed_tool_references`，不会丢失 MCP Tool allowlist；
- Workflow Definition 已增加一等 `inputs_schema / tags`，`workflow_list` 直接返回 discovery 所需的 description、输入契约和标签；
- Workflow description 在 Definition 领域入口必填，Authoring、Store 与 Runtime 使用同一合法性标准；
- `workflow_start.inputs` 在创建 Run 前按 Workflow `inputs_schema` 真实校验，required input 缺失会拒绝启动；
- `workflow_authoring_context` 暴露 `workflow_contract` 以及 Prompt / Skill / Effective Tool / MCP Connection / Workflow Catalog；
- AI 端到端 Contract 已覆盖 `prompt_save -> skill_save -> workflow_validate -> workflow_save -> workflow_list -> workflow_start`；
- stale AI Workflow update 返回 `WORKFLOW_VERSION_CONFLICT`，加载当前版本后可按 expected_version 正常更新；
- AI Runtime 保存 Workflow 后 Desktop Catalog 可立即读取同一份 description、inputs_schema、tags、metadata 与 Graph Node 引用；
- Vue Flow round-trip 保留 `inputs_schema / tags / metadata`，GUI 再保存不会丢失 AI 创建的 discovery 数据；
- Workflow Inspector 已提供 Schema-driven Arguments 表单，Prompt / Skill / System Tool / MCP Tool 的常用参数无需手写 JSON；复杂配置保留 Advanced JSON；
- Workflow Inputs Schema 已提供可视化 Object Schema Builder，支持参数名、类型、说明、Required 与 Additional Properties，复杂 Schema 保留 Advanced JSON Schema；
- Node Library 与 Capability Inspector 已提供搜索；空 Canvas 提供直接进入 Node Library 的空状态；
- Workflow Editor 已提供 Undo / Redo、Duplicate Node、删除确认；历史记录直接保存 Workflow Definition snapshot，不建立第二套 Graph State；
- Python Validator 的 `subject` 已映射到 Canvas/Inspector，节点错误显示数量徽标与错误 ring，并能就地查看具体错误；Prompt / Skill / MCP Tool 引用失效也能直接定位；
- 0.3.x 发布前内部试验兼容层已删除：Prompt/Skill 不读取 Workspace 资产，Skill 不接受持久化 `allowed_tools: string[]`，Workflow 不接受 Test Node，不存在 Global Workflow 层；
- 当前 Definition 必须显式携带当前 `schema_version`；缺失、0 和其他预发布试验版本被拒绝，future schema 被保护性跳过；
- 损坏 Prompt / Skill / Workflow / MCP Connection Catalog 条目会 quarantine；future schema 只 skip 不 quarantine；
- MCP rediscovery 失败保留最后一次成功 Tool Catalog，并记录 `last_error`；HTTPError response handle 已显式关闭，MCP `ResourceWarning-as-error` 测试通过；
- Permission / Sandbox / Secret / Approval / optimistic concurrency Contract 均保持；第三方 MCP Server 的协议版本互操作能力继续保留，不与内部 Workbench 数据兼容混为一谈；
- 大型 Graph 门禁验证 5,000-node DAG；大型 Catalog 门禁验证 2,000 Workflow list + summary；
- 前端 `pnpm test` 4 tests PASS，包含 System/MCP Tool Reference round-trip；
- 前端 `pnpm run build` 已实际完成 `tsc --noEmit + vue-tsc --noEmit + vite build` 并 PASS；
- Phase 10–15 Workbench / Desktop / MCP / Approval 综合后端回归 105 tests PASS；
- MCP Connection `ResourceWarning-as-error` 4 tests PASS，Tool Framework 8 tests PASS，Python `compileall` PASS；
- 项目级 Python 全量门禁 323 tests PASS、3 skipped；Git Contract 通过隔离 `GIT_CONFIG_GLOBAL` 真实执行，当前 Seatbelt 无 PTY 时 TTY Contract 明确 skip；
- 前端 `pnpm test` 4 tests PASS；`pnpm run build` 已实际完成 `tsc --noEmit + vue-tsc --noEmit + vite build` 并 PASS。

# MCP Tools Server Framework Architecture

## 1. 设计原则

`agent_runtime` 按**功能领域组织工具实现**，权限作为横切策略层管理，不按 `safe / trusted / dangerous` 把工具文件拆散。

三个核心概念必须保持独立：

```text
ToolDefinition
    表示“工具是什么、Schema 是什么、由谁处理”

Capability
    表示“这个工具具有什么安全能力”

PermissionProfile / OperationPermission
    表示“当前运行模式默认允许什么，以及一次具体调用还需要什么临时授权”
```

因此不要出现：

```text
tools/safe/
tools/trusted/
tools/dangerous/
```

一个 Git、文件或进程模块的代码应该始终放在自己的功能领域中。

## 2. 当前框架结构

```text
agent_runtime/
├── core/
│   ├── constants.py     # Server identity / endpoint constants
│   ├── tool.py          # ToolDefinition / ToolAnnotations
│   ├── registry.py      # ToolRegistry
│   └── dispatcher.py    # ToolDispatcher
├── tools/
│   ├── system/
│   │   ├── definitions.py
│   │   └── handlers.py
│   ├── filesystem/
│   │   ├── definitions.py
│   │   └── handlers.py
│   ├── process/
│   │   ├── definitions.py
│   │   ├── handlers.py
│   │   └── policy.py
│   ├── git/
│   │   ├── definitions.py
│   │   └── handlers.py
│   └── toolchains/
│       ├── definitions.py
│       └── handlers.py
├── permissions/
│   ├── capabilities.py  # Capability / OperationPermission / PermissionProfile
│   ├── context.py       # Active Permission Context
│   ├── policy.py        # PermissionPolicy
│   ├── state.py         # signed requestState + replay protection
│   ├── grants.py        # once/session grant store
│   ├── broker.py        # Desktop Permission Broker adapter
│   └── session.py       # per-Runtime/Profile permission lifecycle
├── sandbox/
├── toolchains/
├── oauth.py
├── protocol.py
├── server.py
└── runtime.py
```

公开 Tool Handler 已全部从 `runtime.py` 迁入功能模块。权限生命周期也由独立 `PermissionSession` 管理。`runtime.py` 只通过 Mixin 组合 Handler，并持有 Workspace、Sandbox、PermissionSession、Toolchain Snapshot、Registry 与 Dispatcher 等顶层运行时对象。

架构测试要求 20 个公开 Tool 名称不能重新出现在 `Runtime.__dict__` 中，避免后续功能增长时再次把业务实现堆回组合层。

## 3. ToolDefinition

每个工具通过统一定义描述：

```python
ToolDefinition(
    name="read_file",
    title="Read file",
    description="Read a UTF-8 text file slice inside the configured workspace.",
    input_schema=...,
    handler_name="read_file",
    capabilities=frozenset({Capability.FILESYSTEM_READ}),
    annotations=ToolAnnotations(
        read_only=True,
        idempotent=True,
    ),
)
```

其中 `annotations` 是 MCP Tool Metadata，`capabilities` 是本项目安全模型。两者不能互相替代。

## 4. Capability 与 OperationPermission

Capability 描述工具本身的能力，例如：

```text
system.inspect
filesystem.read
filesystem.write
process.execute
process.control
git.read
toolchain.discover
permission.manage
media.read
```

OperationPermission 描述某一次调用需要临时升级的操作，例如：

```text
network
destructive_command
git_metadata_write
long_timeout
sensitive_env
sandbox_env_override
shell_expansion
inline_script
privileged_executable
write_generated_or_ignored
```

例如 `exec_command` 的 Tool Capability 是 `process.execute`，但只有当具体命令需要联网、修改 `.git` 或执行内联脚本时，才额外触发对应 OperationPermission。

## 5. PermissionProfile

当前保留三种用户可见模式：

```text
safe
trusted
dangerous
```

Profile 只是策略集合，不决定工具属于哪个目录。

- `safe`：工具在 Workspace / Sandbox 边界内工作，需要升级的操作逐次授权。
- `trusted`：保留 Sandbox，并按当前策略自动允许网络等可信开发能力；Home Path Expansion 等敏感边界仍遵循独立 OperationPermission / Sandbox 规则。
- `dangerous`：所有 OperationPermission 自动允许，并使用现有 dangerous 执行模型。

未来增加 `readonly`、`ci`、`automation` 等 Profile 时，只新增策略组合，不移动任何工具源码。

## 6. PermissionSession

每个 Runtime/Profile 都拥有独立的 `PermissionSession`：

```text
PermissionSession
├── PermissionStateStore
│   ├── requestState 签名
│   ├── tool + arguments 绑定
│   ├── workspace 绑定
│   ├── principal 绑定
│   └── single-use replay protection
├── PermissionGrantStore
│   ├── once grant
│   └── session grant by principal
└── PermissionBroker
    └── Desktop Permission Broker adapter
```

因此权限状态不是全局状态，也不属于 Tool Handler。Local MCP Gateway 同时挂载多个 Profile 时，每个 Profile 都使用自己的 `PermissionSession`，禁止跨 Profile 共享 requestState、grant 或 session-all approval。

`runtime.local_permission_broker` 目前仅作为历史兼容 facade 保留，实际 Broker client 由 `PermissionSession` 持有。

## 7. ToolRegistry 与 ToolDispatcher

调用链为：

```text
MCP tools/call
    ↓
ToolDispatcher
    ↓
ToolRegistry.resolve
    ↓
JSON Schema validation
    ↓
PermissionPolicy capability check
    ↓
PermissionSession round / grants / Broker
    ↓
Sandbox
    ↓
Handler
    ↓
structured Tool Result
```

`ToolRegistry` 禁止重复工具名。可选能力通过 Feature Gate 控制，例如 `view_image`。

## 8. Handler 组织规则

当前 20 个公开 Tool Handler 已完成按领域迁移：

```text
tools/filesystem/
├── handlers.py
└── definitions.py

tools/process/
├── handlers.py
├── definitions.py
└── policy.py
```

`runtime.py` 只负责：

- Workspace Runtime Context
- PermissionSession 顶层组合对象
- Sandbox 生命周期
- Toolchain Snapshot
- ToolRegistry / Dispatcher 组合
- MCP request execution boundary

具体 Tool 业务逻辑禁止直接新增到 `Runtime`。如果某个领域继续变大，优先在对应功能包内增加 `services.py`、`models.py` 或 `manager.py`，不要把实现重新搬回 Runtime。

## 9. Local MCP Gateway

Gateway 应依赖 Runtime/Registry，而不是复制工具实现：

```text
Local MCP Gateway
        │
    path -> profile
        │
┌───────┼────────┐
/crm  /home  /project
  │      │       │
Runtime Runtime Runtime
  └──────┼───────┘
      Shared Tool Framework
```

每个 Profile 拥有独立 Workspace、PermissionProfile、PermissionSession、OAuthService/Registry 与 Desktop Broker identity，但共享同一套 ToolDefinition 与 Handler 实现。

该架构已经落地为：

```text
agent_runtime/gateway/
├── models.py
├── routes.py
├── registry.py
├── runtime_pool.py
└── config.py

agent_workbench/
├── gateway_process.py
├── gateway_launcher.py
├── gateway_profiles.py
└── gateway_manager.py
```

`MCPHTTPServer` 支持显式 `gateway_pool` 模式；默认单 Runtime 构造方式保持兼容。详细设计见 `docs/LOCAL_MCP_GATEWAY.md`。

## 10. 新增工具规范

新增工具时：

1. 选择功能领域，不按权限等级选择目录。
2. 定义 input Schema。
3. 明确 Capability。
4. 独立声明 MCP annotations。
5. 注册到对应 `*_TOOLS` 集合。
6. 由 `ToolRegistry` 检查唯一名称。
7. 为 Schema、Capability、权限升级和 Handler 增加 Contract Test。

禁止通过工具名称、目录名称或 MCP `readOnlyHint` 推断真实安全权限。

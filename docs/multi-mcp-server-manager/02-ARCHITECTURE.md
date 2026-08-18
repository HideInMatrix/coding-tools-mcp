# 架构设计

## 1. 目标架构

```text
Desktop MainWindow
    |
    v
MCPServerManager
    |
    +-- ServerRuntime(server-A)
    |     +-- MCPServerProcess
    |     +-- NetworkProvider
    |     +-- OAuth storage
    |
    +-- ServerRuntime(server-B)
    |     +-- MCPServerProcess
    |     +-- NetworkProvider
    |     +-- OAuth storage
    |
    +-- ServerRuntime(server-C)
          +-- MCPServerProcess
          +-- NetworkProvider
          +-- ephemeral OAuth session
```

## 2. 模块职责

### ServerProfileStore

负责：

- 保存 Server Profile。
- 读取所有 Profile。
- 新建、更新、删除 Profile。
- 自动分配默认端口。
- 保持 `server_id` 稳定。

不负责启动进程。

### MCPServerManager

负责：

- 同时管理多个 Server Runtime。
- `start(server_id)`。
- `stop(server_id)`。
- `stop_all()`。
- 查询运行状态。
- 防止重复启动同一 Server。

### ServerRuntime

每个 Server 一个实例。

负责：

- 创建 Network Provider。
- 启动 MCP Process。
- 准备该 Server 的 OAuth 环境变量。
- 监听子进程退出。
- 停止本 Server 的全部子进程。

### OAuthPersistence

Persistent Server：

```text
server_id
  -> servers/<server_id>/oauth/clients.json
  -> servers/<server_id>/oauth/token-secret
```

Ephemeral Quick Tunnel：

```text
runtime/<server_id>/<session_id>/oauth/...
```

停止 Session 后删除。

## 3. 身份层级

```text
Desktop Installation
    |
    +-- server_id A
    |     +-- client_id A1
    |     +-- client_id A2
    |
    +-- server_id B
          +-- client_id B1
```

`server_id` 是本地 Server 身份。

`client_id` 是外部 OAuth Client 身份。

二者不能互换。

## 4. Public URL 与 Server ID

旧模型：

```text
public_base_url -> hash -> OAuth Registry
```

新模型：

```text
server_id -> OAuth Registry
```

Public URL 只是 Server 当前网络入口，不再承担持久化身份职责。

## 5. UI 结构

左侧建议：

```text
服务
授权客户端
关于
```

服务页面：

- Server 卡片列表。
- 新建 Server。
- 编辑 Server。
- 启动/停止。
- 显示 Workspace、Port、Network、Public URL、Client 数量。

授权客户端页面：

- Server 下拉选择。
- Client 列表。
- 撤销 Client。
- 全部撤销。

## 6. 兼容策略

现有 `MCPLauncher` 在过渡期可以继续保留，内部逐步改造成单 `ServerRuntime` 封装。

最终 `MCPServerManager` 持有多个 Runtime，而不是 MainWindow 直接持有一个全局 Launcher。

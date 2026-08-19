# Local MCP Gateway

## 1. 目标

Local MCP Gateway 用于**同一台机器**上，一个公网 hostname、一个 Tunnel、一个本地监听端口承载多个 MCP Profile。

例如：

```text
https://mcp.example.com/company/mcp
https://mcp.example.com/home/mcp
https://mcp.example.com/project-a/mcp
```

这些 Path 不负责选择 Cloudflare Tunnel。请求已经通过同一个 hostname/Tunnel 到达本机后，Local MCP Gateway 再按 Path 选择 Profile。

```text
ChatGPT
   ↓
https://mcp.example.com
   ↓
Cloudflare Named Tunnel
   ↓
127.0.0.1:8234
   ↓
Local MCP Gateway
   ├── /company  → Company Runtime
   ├── /home     → Home Runtime
   └── /project  → Project Runtime
```

因此 Cloudflare Dashboard 中 Published Application 的 Path 保持为空即可：

```text
Hostname: mcp.example.com
Path:     <empty>
Service:  http://127.0.0.1:8234
```

## 2. 与直连 Server 的区别

### Direct Server

适合不同电脑或完全独立的入口：

```text
company.mcp.example.com → Company Computer → MCP Server
home.mcp.example.com    → Home Computer    → MCP Server
```

一个直连 Server 拥有：

- 一个本地端口
- 一个 Network Provider
- 一个 Tunnel/Public Hostname
- 一个 Workspace
- 一个 PermissionSession
- 一个 OAuth Registry

### Local Gateway

适合同一台电脑：

```text
mcp.example.com/company/mcp
mcp.example.com/home/mcp
```

一个 Gateway 拥有：

- 一个本地端口
- 一个 Network Provider
- 一个 Tunnel/Public Hostname
- 多个 Member Profile

每个 Member Profile 独立拥有：

- stable `server_id`
- `instance_path`
- Workspace
- PermissionProfile
- PermissionSession
- OAuth issuer
- OAuth token secret
- OAuth Client Registry
- Desktop Permission Broker identity

## 3. Gateway 路由

Gateway 不只路由 `/mcp`，还必须把 OAuth discovery 与 endpoint 路由到同一个 Profile。

以 `/company` 为例：

```text
/company/mcp
/company/oauth/authorize
/company/oauth/token
/company/oauth/register

/.well-known/oauth-authorization-server/company
/.well-known/openid-configuration/company
/company/.well-known/openid-configuration
/.well-known/oauth-protected-resource/company/mcp
```

这些地址必须归属于同一个 Company Runtime，避免 OAuth issuer、DCR Client 或 token 串到其他 Profile。

嵌套 Path 使用 longest-prefix 匹配：

```text
/team
/team/dev
```

请求 `/team/dev/mcp` 必须选择 `/team/dev`。

## 4. Runtime 隔离

`GatewayRuntimePool` 为每个 Profile 创建独立 Runtime：

```text
GatewayRuntimePool
├── company
│   └── Runtime
│       └── PermissionSession
└── home
    └── Runtime
        └── PermissionSession
```

以下状态禁止跨 Profile 共享：

- requestState
- once permission grant
- session-all permission grant
- authenticated principal permission context
- OAuth Client Registry
- OAuth token secret
- Desktop Permission Broker `server_id`

工具定义、ToolRegistry、Handler 代码可以共享，因为它们是无 Profile 状态的 Framework 代码。

## 5. PermissionSession

每个 Runtime/Profile 自己拥有 `PermissionSession`：

```text
PermissionSession
├── PermissionStateStore
├── PermissionGrantStore
└── PermissionBroker
```

requestState 会绑定：

```text
tool
arguments digest
workspace
authenticated principal
expiration
nonce
```

并执行 single-use replay protection。

Desktop Permission Broker 在 Gateway 中也按 Profile 使用不同 `server_id`。因此授权弹窗可以准确显示正在请求权限的 Profile，而不是只显示 Gateway 进程。

## 6. OAuth 隔离

旧架构中的 OAuth Client Registry persistence 曾由进程级环境变量控制：

```text
CODING_TOOLS_MCP_OAUTH_CLIENT_REGISTRY_FILE
```

这不适合 Gateway，因为一个进程内有多个 Profile。

现在 `OAuthClientRegistry` 自身拥有可选的 `persistence_file`：

```text
Company OAuthConfig
└── OAuthClientRegistry(company/clients.json)

Home OAuthConfig
└── OAuthClientRegistry(home/clients.json)
```

Registry 使用原子写入：

```text
mkstemp
→ write
→ fsync
→ os.replace
```

单 Profile Launcher 原有环境变量仍兼容，但旧 monkeypatch 在新版 Server 上成为 no-op。

## 7. Gateway 启动链路

桌面端链路：

```text
MCPGatewayManager
    ↓
MCPGatewayLauncher
    ├── NetworkProvider
    └── GatewayServerProcess
            ↓
        mcp_worker
            ↓
        mcp_tools_server.server
            ↓
        --gateway-config <temporary-json>
```

Gateway 临时配置包含每个 Profile 的运行时 OAuth secret 和 Broker identity，因此：

- POSIX 临时目录权限为 `0700`
- JSON 文件权限为 `0600`
- 子进程完整读取配置并成功监听端口后，Launcher 立即删除临时 JSON
- persistent OAuth state 存放在各自 issuer storage
- ephemeral Session 停止后清理

## 8. Quick Tunnel

Cloudflare Quick Tunnel 或没有固定 Public URL 的 ngrok 地址在每次启动时可能变化。

Gateway Launcher 会将所有 Profile 的 OAuth lifecycle 自动切换为：

```text
ephemeral
```

例如本次启动得到：

```text
https://random.trycloudflare.com
```

则实际 MCP URL 为：

```text
https://random.trycloudflare.com/company/mcp
https://random.trycloudflare.com/home/mcp
```

停止本次 Gateway Session 后，临时 OAuth 状态随 Session 清理。

固定 Named Tunnel hostname 则保持 persistent issuer storage。

## 9. Desktop Profile Model

桌面端不复用 `MCPServerProfile` 表示 Gateway。

当前持久化资源分为：

```text
servers.json
    └── Direct MCP Server Profiles

gateways.json
    └── Local MCP Gateway Profiles
        └── members[]
```

这样可以保持两种模式的约束清晰。

### Direct 约束

- 不同机器/Server 使用独立 Public Hostname
- 一个 Profile 一个本地端口
- Cloudflare Path 不参与跨 Tunnel 路由

### Gateway 约束

- 一个 Gateway 一个 Public Hostname
- 一个 Gateway 一个本地端口
- Member Path 在 Gateway 内必须唯一
- Member `server_id` 全局稳定且唯一

DesktopAPI 会同时检查 Direct Server 与 Gateway 的本地端口和固定 Public Hostname，防止两种模式争抢同一资源。

## 10. Desktop UI

左侧导航包含独立的 `Gateway` 页面。

Gateway 页面负责：

- 创建/删除 Gateway
- Network Provider
- Public Hostname
- Tunnel Token
- 本地端口
- Member 增删
- Member Path
- Member Workspace
- Member OAuth Password
- Member Permission Profile
- 启动/停止 Gateway
- 显示并复制每个最终 `/path/mcp` 地址

直连 Server 页面仍保持独立。“实例 Path（高级）”只用于兼容已有 Path-aware Server；同一机器真正的多 Profile 场景应使用 Gateway 页面。

## 11. 当前安全边界

Gateway 解决的是**单机多 Profile 本地分流**，不是跨电脑路由。

以下架构仍然是错误的：

```text
同一个 hostname
同一个 Tunnel UUID/Token
分别在公司和家里电脑启动
然后期待 /company 与 /home 决定进入哪台电脑
```

Cloudflare 会把同 Tunnel UUID 的多个 connector 当作 replicas，而不是根据 URL Path 选择电脑。

不同电脑仍应使用不同 hostname/Tunnel；或者另行部署真正的云端 Edge Router。


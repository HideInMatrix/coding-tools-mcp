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

从产品模型看，用户只管理一个 `Service`。服务至少包含一个 Workspace Profile：

```text
Service
├── Network / Hostname / Port
└── Profiles[]
    ├── path=""        -> 主 Workspace -> /mcp
    ├── path="/api"    -> API Workspace -> /api/mcp
    └── path="/web"    -> Web Workspace -> /web/mcp
```

Service 持久化一个显式运行模式：`single` 或 `multi`。Profile 数量本身不决定运行模式。用户可以提前配置多个子 Profile，再切回 `single`；这些配置继续保留，但本次只启动根 Workspace。只有显式选择 `multi` 时，启动层才使用 Local Gateway / RuntimePool。

### Local Gateway 内部实现

适合同一台电脑：

```text
mcp.example.com/company/mcp
mcp.example.com/home/mcp
```

内部 Gateway 拥有：

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
AGENT_RUNTIME_OAUTH_CLIENT_REGISTRY_FILE
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

桌面端“OAuth 授权”页面按 Service/Profile 统一展示授权目标：

```text
服务 · Company / 主 Workspace
服务 · Company / API
服务 · Company / Web
```

服务运行期间允许实时查看各 Profile 的 DCR Client，但不直接修改运行中的 Registry。persistent Profile 需要先停止服务后再撤销 Client；ephemeral Profile 的 Client 会随 Session 停止自动销毁。

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
        agent_runtime.server
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

## 9. Desktop Service Model

产品层使用统一 Service/Profile 模型。当前为了兼容已有安装，磁盘层暂时仍可读取两种历史存储：

```text
servers.json
    └── Direct MCP Server Profiles

gateways.json
    └── Local MCP Gateway Profiles
        └── members[]
```

新 UI 不暴露这两种存储差异。旧 Direct Server 自动映射成一个 `path=""` 的主 Profile；旧 Gateway 自动映射成多个已有 Profile。

当旧 Direct Service 第一次添加子 Profile 时，DesktopAPI 会无损提升其内部运行模型：

```text
原 server_id -> 根 Profile server_id 保持不变
https://host/mcp -> 保持不变
OAuth issuer https://host -> 保持不变
新增 /api -> https://host/api/mcp
```

因此增加子 Profile 不需要重新创建原来的 Connector。

## 10. Desktop UI

桌面端只保留一个一级“服务”入口，也不再要求用户选择运行模式：

```text
服务
├── 主 Workspace -> /mcp
├── [添加 Profile] -> /api/mcp
└── [添加 Profile] -> /web/mcp
```

服务页面统一负责：

- 创建/删除服务
- Network Provider
- Public Hostname
- Tunnel Token
- 本地端口
- Profile 增删
- Profile Path
- Profile Workspace
- Profile OAuth Password
- Profile Permission Profile
- 启动/停止服务
- 显示并复制每个最终 `/path/mcp` 地址
- 触发公网 E2E 自检并展示每个 Profile 的检查结果

主 Workspace 不需要填写 Path，固定使用 `/mcp`。子 Profile 才配置 `/api`、`/web` 等 Path。服务页顶部使用“单 Workspace / 多 Workspace”分段滑块显式保存运行模式：`single` 走 `MCPLauncher` 单 Runtime，`multi` 才走 Gateway RuntimePool。切换模式不会删除任何 Profile 配置。

## 11. 公网 E2E 自检

Gateway 使用现有内部 route-probe 机制验证真正的请求归属。probe 需要随机 Session Token，不接受匿名访问，并且只返回 Workspace 路径的 SHA-256 截断指纹，不暴露真实本地路径。

对每个 Member，诊断器检查：

```text
local_path_runtime
public_path_runtime
server_card
oauth_authorization_metadata
oauth_protected_resource
mcp_auth_challenge
```

其中最关键的 `public_path_runtime` 会请求：

```text
https://mcp.example.com/company/.well-known/micromatrix-workbench-route-probe
https://mcp.example.com/home/.well-known/micromatrix-workbench-route-probe
```

并将返回的 Workspace fingerprint 与桌面端当前 Member Workspace 的 fingerprint 比较，因此可以区分“公网确实到了 Gateway”与“公网 Path 确实到了正确 Runtime”。

固定 Cloudflare Named Tunnel 的多 Profile 服务启动成功后会自动后台执行公网 E2E 自检。自检失败只记录警告，不会把健康的服务/Tunnel 强制停止。“服务”页面中也提供手动“开始自检”按钮。

OAuth 部分同时验证：

- Server Card 的 `/path/mcp` endpoint
- Authorization Server issuer
- authorization/token endpoint 是否带正确 Profile Path
- RFC 9728 protected resource 是否为 `/path/mcp`
- 未授权访问 `/path/mcp` 时 `WWW-Authenticate` 的 `resource_metadata` 是否指向当前 Profile

## 12. 当前安全边界

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


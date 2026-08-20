# OAuth client_id 生命周期与多 MCP Server 存储流程

本文说明 MicroMatrix Workbench 桌面端中 `server_id`、OAuth `issuer`、MCP `resource`、`client_id`、OAuth Registry、固定域名和临时 Tunnel 之间的关系。

## 1. 先区分两个 ID

### server_id

`server_id` 是桌面程序创建 MCP Server Profile 时生成的本地永久身份。

例如：

```text
MCP Server A
server_id = 4f8c...
port = 8234

MCP Server B
server_id = a972...
port = 8235
```

修改服务名称、Workspace、端口或 Public URL 都不会自动生成新的 `server_id`。

删除 Server Profile 后，这个 `server_id` 废弃，不复用。

### issuer 与 resource

`issuer` 是 OAuth Authorization Server 的身份，也是 DCR Client Credential 的真正归属边界。

例如固定 MCP 地址：

```text
issuer   = https://mcp-a.example.com
resource = https://mcp-a.example.com/mcp
```

`server_id` 只用于桌面管理，不再作为 OAuth Registry 的身份边界。

### client_id

`client_id` 可以来自两种机制：

- DCR：服务端生成随机 `client_id`，需要按 `issuer` 持久化。
- CIMD：`client_id` 本身是 HTTPS Metadata Document URL，服务端按需读取，不写入 DCR Registry。

它不是桌面程序的身份，也不是 Cloudflare Tunnel ID。

一个 MCP Server 可以拥有多个 OAuth Client：

```text
Server A
  ├── client_id = A1
  ├── client_id = A2
  └── client_id = A3

Server B
  └── client_id = B1
```

桌面端不提供手工 Client ID / Client Secret 配置。

## 2. Persistent Server 的 client_id 从哪里来

假设用户创建：

```text
名称       公司项目
server_id  SERVER-A
端口       8234
Public URL https://mcp-a.example.com
```

启动后：

```text
Desktop
  ↓
读取 Server Profile SERVER-A
  ↓
确定 OAuth issuer = https://mcp-a.example.com
  ↓
启动 Network Provider
  ↓
准备 issuer 专属 OAuth 存储
  ↓
启动本地 MCP Server :8234
```

Persistent Server 的 OAuth 文件位于：

```text
settings_dir/
└── oauth/
    └── issuers/
        └── <sha256(canonical-issuer)>/
            ├── issuer.json
            ├── clients.json
            └── token-secret
```

Server Profile 只保存一个到 issuer 的管理绑定，用于桌面端显示/撤销 DCR Client；OAuth 数据本身不属于 `server_id`。

随后 AI 创建一个新的 MCP 连接：

```text
AI / MCP Client
  ↓
读取 OAuth metadata
  ↓
发现 registration_endpoint=/oauth/register
  ↓
POST /oauth/register
```

服务端的 `OAuthClientRegistry.register()`：

```text
校验 redirect_uris
  ↓
校验 grant_types / response_types
  ↓
生成随机 client_id
  ↓
写入内存 Registry
  ↓
写入 issuer 对应的 clients.json
  ↓
HTTP 201 返回 client_id
```

例如返回：

```text
client_id = A1
```

然后 AI 使用同一个 A1 发起：

```text
/oauth/authorize?client_id=A1&...
```

服务端执行：

```text
registry.get(A1)
```

存在则继续 OAuth；不存在则返回：

```text
Unknown client_id
```

## 3. 为什么 Persistent Server 重启后 client_id 仍然存在

停止 Server A 时，只要固定域名 `issuer` 不变：

```text
oauth/issuers/<issuer-hash>/clients.json
```

仍然保留。

再次启动同一 issuer，即使本地 Server Profile 因升级而重建：

```text
issuer = https://mcp-a.example.com
  ↓
重新打开 issuer 对应 clients.json
  ↓
恢复 A1、A2、A3
```

因此 Persistent DCR Client 生命周期跟 OAuth Authorization Server `issuer` 绑定，而不是跟本地 `server_id`、端口或某次桌面进程绑定。

## 4. 删除 MCP-A 后重新创建 MCP-B

假设第一次在 AI 中创建连接 MCP-A：

```text
POST /oauth/register
  ↓
client_id = A1
```

Server Registry：

```text
A1
```

用户停止桌面程序，然后在 AI 中删除 MCP-A。

删除 AI 侧连接不会自动通知 MicroMatrix Workbench 删除 A1，因此磁盘 Registry 中 A1 仍可能存在。

这不是错误。

用户随后创建 MCP-B，正常流程必须再次执行：

```text
POST /oauth/register
  ↓
client_id = A2
```

此时：

```text
SERVER-A Registry
  ├── A1
  └── A2
```

MCP-B 应使用：

```text
/oauth/authorize?client_id=A2
```

A1 的存在不会阻止 A2 注册，也不会导致 A2 出现 `Unknown client_id`。

## 5. Unknown client_id 真正表示什么

`Unknown client_id` 的直接含义只有一个：

```text
/oauth/authorize 收到 client_id=X
  ↓
当前 Server Registry 中找不到 X
```

Persistent Server 中需要排查：

1. 新连接是否真正调用了 `/oauth/register`。
2. `/oauth/register` 返回的 ID 是否就是 authorize 使用的 ID。
3. register 成功后 `clients.json` 是否成功持久化。
4. 当前 Authorization Server `issuer` 是否与注册 client_id 时一致。
5. 当前是否加载了该 issuer 对应的 Registry，以及旧 server-id/URL 状态是否完成迁移。
6. 用户是否已经在“授权客户端”页面主动撤销该 Client。

如果 `client_id` 是 HTTPS URL，则它属于 CIMD，不应在 DCR Registry 中查找；服务端会按需拉取并校验 Client Metadata Document。

禁止用下面的方法“修复”：

```text
authorize 收到未知 client_id
  ↓
自动把它加入 Registry
```

因为 `/oauth/authorize` 无法恢复注册阶段的 redirect URI、认证方式等 Client Metadata，这会破坏 OAuth 安全边界。

## 6. 授权客户端管理

桌面端“授权”页面按照 MCP Server 展示已注册 OAuth Client。

Persistent Server 可以显示：

```text
Client Name
Client ID
Redirect URI
Token Endpoint Auth Method
Issued At
```

不会显示：

```text
client_secret
secret_digest
access token
refresh token
authorization code
```

当前实现要求先停止 Persistent Server，再执行撤销，以避免桌面父进程和 MCP 子进程同时修改 `clients.json`。

撤销 A1 后：

```text
clients.json 删除 A1
  ↓
重新启动 Server
  ↓
registry.get(A1) = None
```

旧 A1 的 access token 也会验证失败，因为 access token 校验会再次检查该 `client_id` 是否仍存在于当前 Registry。

如果用户需要重新连接，在 AI 中重新创建 MCP，客户端再次调用 `/oauth/register` 获得新的 client_id 即可。

## 7. 多 MCP Server 之间如何隔离

例如：

```text
Server A
server_id = SERVER-A
port = 8234
https://a.example.com

Server B
server_id = SERVER-B
port = 8235
https://b.example.com
```

OAuth 状态按 issuer 隔离：

```text
oauth/issuers/
├── <hash(https://a.example.com)>/
│   ├── issuer.json
│   ├── clients.json
│   └── token-secret
└── <hash(https://b.example.com)>/
    ├── issuer.json
    ├── clients.json
    └── token-secret
```

因此：

```text
A1 只属于 issuer https://a.example.com
B1 只属于 issuer https://b.example.com
```

不能把 A1 拿到 Server B 的 `/oauth/authorize` 使用。

## 8. Cloudflare Named Tunnel

固定域名场景建议使用 Persistent Server。

例如 Cloudflare Published Application：

```text
mcp-a.example.com -> http://127.0.0.1:8234
mcp-b.example.com -> http://127.0.0.1:8235
```

桌面端分别保存：

```text
Server A Public URL = https://mcp-a.example.com
Server B Public URL = https://mcp-b.example.com
```

每个本地 Server 拥有自己的 `server_id`；DCR OAuth Registry 则由对应固定域名的 `issuer` 决定。

## 9. Cloudflare Quick Tunnel

Quick Tunnel 属于临时 Session。

第一次启动：

```text
Server TEMP
port = 8236
  ↓
cloudflared --url http://127.0.0.1:8236
  ↓
https://aaa.trycloudflare.com
```

本次 Session 创建独立临时 OAuth Registry：

```text
POST /oauth/register
  ↓
client_id = Q1
```

运行期间，“授权”页面可以查看 Q1。

停止 Server 后：

```text
cloudflared 停止
  ↓
aaa.trycloudflare.com 失效
  ↓
临时 OAuth Registry 删除
  ↓
Q1 失效
```

下一次启动可能得到：

```text
https://bbb.trycloudflare.com
```

AI 必须重新调用 `/oauth/register`，得到新的 Q2。

旧 Q1 不迁移到新的随机 URL。

## 10. 从旧 Registry 升级

旧桌面版本曾按照 `public_base_url` 的哈希存储：

```text
oauth/
├── <url-hash>.clients.json
└── <url-hash>.token-secret
```

当前版本在每次 Persistent Server 启动时执行幂等迁移，而不是只在第一次创建 Server Profile 时执行：

```text
确定当前 canonical issuer
  ↓
检查 oauth/issuers/<issuer-hash>/
  ↓
如缺失，先检查 servers/<server_id>/oauth/
  ↓
再检查旧 URL-hash OAuth 文件
  ↓
复制缺失文件到 issuer 目录
```

迁移同时复制：

```text
clients.json
token-secret
```

这样旧动态注册 client_id 可以继续恢复，原有 token secret 也不会因为升级立即变化。

迁移是非破坏、幂等的：

- 不删除旧 URL-hash 文件。
- 不覆盖已经存在的 issuer 文件。
- Quick Tunnel 的旧随机 URL OAuth 状态不迁移。

## 11. 推荐诊断日志

后续定位 OAuth 问题时，至少应能确认：

```text
server_id
Server 名称
本地 host:port
当前 public_base_url
OAuth issuer
MCP resource
当前 OAuth Registry 文件
启动时恢复 Client 数量
收到 POST /oauth/register
生成的 client_id
收到 /oauth/authorize 的 client_id
client_id 是否存在于当前 Registry
```

不得记录：

```text
OAuth Password
Client Secret
Access Token
Refresh Token
Authorization Code
Cloudflare Tunnel Token
```

## 12. 最终关系图

```text
MicroMatrix Workbench Desktop
│
├── Server A
│   server_id = SERVER-A
│   port = 8234
│   lifecycle = persistent
│   issuer = https://a.example.com
│
├── Server B
│   server_id = SERVER-B
│   port = 8235
│   lifecycle = persistent
│   issuer = https://b.example.com
│
└── Server TEMP
    server_id = SERVER-TEMP
    port = 8236
    lifecycle = ephemeral
    │
    └── Current Quick Tunnel Session
        └── Q1
            ↓
        Server stop 后销毁

Persistent OAuth Identity
│
├── issuer https://a.example.com
│   resource https://a.example.com/mcp
│   └── DCR Registry: A1, A2
│
└── issuer https://b.example.com
    resource https://b.example.com/mcp
    └── DCR Registry: B1
```

核心原则：

> `server_id` 只标识本地 MCP Server Profile；Persistent DCR Client Credential 跟 OAuth `issuer` 绑定，MCP token 的 audience 跟具体 `/mcp` `resource` 绑定；CIMD client_id 由 HTTPS Metadata Document 自描述；临时 Tunnel Client 跟当前临时 issuer Session 绑定。

# 需求分析

## 1. 背景

当前桌面程序的运行模型是：

```text
Desktop
  -> MCPLauncher
     -> 1 个 MCP Server
     -> 1 个 Network Provider
     -> 默认端口 8234
```

这导致以下限制：

- 一次只能启动一个 Workspace。
- `8234` 实际上成为全局固定端口。
- OAuth Registry 当前依赖 `public_base_url` 选择持久化文件，Quick Tunnel 域名变化时容易造成 Client 状态分裂。
- 桌面端无法查看某个 MCP Server 已注册了多少 OAuth Client。
- 无法主动撤销某个 OAuth Client。
- 固定域名服务和临时 Quick Tunnel 服务使用同一套持久化语义，不够清晰。

## 2. 目标

桌面端升级为本地 MCP Server Manager。

用户可以创建多个 MCP Server Profile，例如：

```text
公司项目  -> 127.0.0.1:8234 -> https://company-mcp.example.com
个人项目  -> 127.0.0.1:8235 -> https://personal-mcp.example.com
临时测试  -> 127.0.0.1:8236 -> https://random.trycloudflare.com
```

每个 Server 独立管理：

- 名称
- Workspace
- Host
- Port
- Network Provider
- 固定或临时生命周期
- OAuth Password
- OAuth Client Registry
- Token Secret
- 运行状态

## 3. Server 生命周期

### 3.1 Persistent Server

用于固定域名、长期使用。

要求：

- `server_id` 永久不变。
- OAuth Registry 永久保存。
- Token Secret 永久保存。
- 重启桌面程序后可恢复原 OAuth Client。
- Public URL 可以由 Cloudflare Named Tunnel、FRP、ngrok、Tailscale、自定义反向代理提供。

### 3.2 Ephemeral Server Session

主要用于 Cloudflare Quick Tunnel。

要求：

- Server Profile 可以保留，例如名称、Workspace、端口。
- 每次启动 Quick Tunnel 会得到新的随机公网 URL。
- 本次 Session 的 OAuth Registry 不跨下一次随机 URL 复用。
- 停止 Server 后清理 Session OAuth 数据。
- 下次启动后 AI 必须重新执行 `/oauth/register`。

## 4. 端口需求

- 默认端口为 `8234`。
- 新建 Server 时优先建议从 `8234` 开始的第一个未被 Profile 占用的端口。
- 用户可以手工修改端口。
- 端口合法范围为 `1..65535`。
- 同时运行的 Server 不允许绑定同一个 `host:port`。
- 固定域名 Server 端口冲突时不允许静默改端口。
- UI 可以提供“自动选择可用端口”。

## 5. Cloudflare 使用需求

### Named Tunnel

用户在 Cloudflare 侧配置：

```text
mcp-a.example.com -> http://127.0.0.1:8234
mcp-b.example.com -> http://127.0.0.1:8235
```

桌面端保存对应 Public URL。

### Quick Tunnel

用户不配置固定域名和 Tunnel Token 时：

```text
127.0.0.1:8236
  -> cloudflared quick tunnel
  -> random.trycloudflare.com
```

停止后公网 URL 失效，OAuth Session 同步失效。

## 6. OAuth Client 管理需求

一个 MCP Server 可以对应多个 OAuth Client：

```text
Server A
  -> ChatGPT Client A1
  -> ChatGPT Client A2
  -> Claude Client A3
```

桌面端需要支持：

- 按 Server 查看 Client 列表。
- 显示 client name、client_id、redirect URI、认证方式、注册时间。
- 删除/撤销单个 Client。
- 清空一个 Server 的全部 Client。
- 删除 Client 后同步更新持久化 Registry。
- 删除 Client 时清理该 Client 的 refresh token。
- 不显示明文 client secret。
- 不提供手工创建 client_id 功能。

## 7. 非目标

本阶段不做：

- 自动登录 Cloudflare Dashboard 修改 Tunnel hostname。
- 把 OAuth Client Secret 明文展示给用户。
- 自动接受 `/oauth/authorize` 中未知 client_id。
- 为未知 client_id 自动补注册。
- 通过端口推导 Server 身份。
- 让多个 Server 共用同一个 OAuth Registry。

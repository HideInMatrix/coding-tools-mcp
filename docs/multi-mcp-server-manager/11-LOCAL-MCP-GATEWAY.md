# Local MCP Gateway 规划

## 1. 定位

Cloudflare 当前采用最简单、最稳定的直连模型：

```text
一个 Public Hostname
        ↓
一个 Named Tunnel
        ↓
一台电脑
        ↓
一个直连 Server Profile
```

例如：

```text
https://company.mcp.example.com/mcp -> 公司电脑
https://home.mcp.example.com/mcp    -> 家里电脑
```

不同电脑使用独立 hostname、独立 Tunnel、独立 Tunnel Token。Cloudflare Published Application 的 Path 保持为空，不使用 Worker/Path Router 做跨电脑分流。

`Path` 能力继续保留，但重新定位为后续 **Local MCP Gateway** 的本地分流键。

## 2. 未来目标

同一台机器上允许多个 Server Profile 共用一个公网 hostname 和一个入口端口：

```text
https://company.mcp.example.com/crm/mcp
https://company.mcp.example.com/frontend/mcp
https://company.mcp.example.com/backend/mcp
```

Cloudflare 仍只需要一条 Published Application：

```text
Hostname: company.mcp.example.com
Path:     空
Service:  http://127.0.0.1:<gateway-port>
```

本地 Gateway 根据 Path 分发：

```text
Cloudflare Tunnel
       ↓
Local MCP Gateway :8234
       ├─ /crm/*      -> Profile CRM
       ├─ /frontend/* -> Profile Frontend
       └─ /backend/*  -> Profile Backend
```

因此 Path 的职责是“同一台机器内选择 Profile”，而不是“选择不同电脑上的 Cloudflare Tunnel”。

## 3. 当前版本行为

当前仍是直连模式，每个 Server Profile 自己启动 MCP Server 进程并监听独立端口。

当前约束：

- 不同电脑：必须使用不同 Public Hostname。
- 同一台电脑的多个直连 Profile：也必须使用不同 Public Hostname。
- `Local Gateway Path（预留）` 可以被 URL/OAuth/HTTP 路由层正确解析，但当前不会把同 hostname 的多个 Profile 自动分发到不同本地端口。
- 因此当前 Profile Store 会拒绝“同 Public Hostname + 不同 Path”的多个直连 Profile，防止产生看似可用、实际无法分流的配置。

## 4. 已保留的 Path-aware 基础能力

服务端已经保留以下能力，后续 Gateway 可以直接复用：

- `https://host/<path>/mcp` 作为 MCP resource。
- `https://host/<path>` 作为 OAuth issuer。
- `/<path>/oauth/authorize`、`/<path>/oauth/token`、`/<path>/oauth/register`。
- RFC 9728 Protected Resource Metadata 的 path-insertion 形式。
- Authorization Server Metadata 的带 Path issuer 形式。
- OAuth Registry / token secret 可按完整 issuer 隔离。

这些能力不应因为当前取消 Cloudflare Path Router 而删除。

## 5. Gateway 建议架构

建议 Gateway 成为桌面主进程管理的独立本地入口，而不是把多个 Profile 强行合并为一个 Runtime：

```text
Local MCP Gateway
  ├─ 路由表: path -> server_id
  ├─ OAuth/MCP 请求入口
  ├─ Profile 生命周期查询
  └─ 转发到独立 Runtime / 本地端口
```

每个 Profile 仍保留：

- 独立 Workspace。
- 独立权限模式。
- 独立 OAuth issuer / Registry。
- 独立 Runtime 生命周期。
- 独立日志和 OAuth Client 管理。

Gateway 只负责入口路由，不应成为所有 Profile 共享权限状态的单体 Server。

## 6. 后续实现阶段

建议按以下顺序实现：

1. 新增 Gateway 配置与统一监听端口。
2. 建立 `path -> server_id` 的显式路由表。
3. Profile 启动时由 Gateway 注册/撤销路由。
4. 支持 MCP、OAuth、`.well-known` 全链路 Path 转发。
5. 放开 Profile Store 当前“Public Hostname 唯一”限制，仅在 Gateway 模式下允许同 hostname + 不同 Path。
6. UI 增加“直连 / Local Gateway”入口模式，避免用户混淆。
7. 增加重复 Path、保留 Path、OAuth issuer 冲突和 Profile 停止后的路由回收测试。

## 7. 验收目标

Gateway 完成后至少满足：

```text
https://company.mcp.example.com/crm/mcp
  -> 只访问 CRM Profile Workspace

https://company.mcp.example.com/frontend/mcp
  -> 只访问 Frontend Profile Workspace
```

并保证：

- 不同 Path 不共享 OAuth Registry。
- 不同 Profile 不共享权限会话。
- 停止某个 Profile 后只移除自己的 Path。
- 一个 Profile 异常退出不影响其他 Path。
- Cloudflare 侧仍然只需要 hostname -> Local Gateway 的单条发布规则。

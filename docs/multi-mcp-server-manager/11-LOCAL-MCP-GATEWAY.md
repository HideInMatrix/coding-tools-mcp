# Local MCP Gateway

Local MCP Gateway 已从规划功能进入实际实现阶段。完整架构、安全边界、OAuth 隔离和运行链路以 [`../LOCAL_MCP_GATEWAY.md`](../LOCAL_MCP_GATEWAY.md) 为准。

## 当前模型

不同电脑继续使用独立 hostname、独立 Named Tunnel 和独立 Tunnel Token：

```text
company.mcp.example.com -> Company Computer
home.mcp.example.com    -> Home Computer
```

同一台机器需要一个 hostname 承载多个 Workspace 时，使用 Local MCP Gateway：

```text
https://mcp.example.com/company/mcp
https://mcp.example.com/home/mcp
        ↓
Cloudflare Named Tunnel
        ↓
127.0.0.1:8234
        ↓
Local MCP Gateway
        ├─ /company -> Company Runtime
        └─ /home    -> Home Runtime
```

Cloudflare Published Application 的 Path 保持为空。URL Path 只在请求已经到达本机 Gateway 后用于选择 Profile。

## 已实现能力

- 独立 `GatewayProfileStore` 与 `MCPGatewayManager`。
- 一个 Gateway Process 同时持有多个独立 Runtime。
- 显式 `instance_path -> profile_id` 路由。
- MCP、OAuth、Authorization Server Metadata、Protected Resource Metadata 全链路 Path-aware。
- 每个 Member 独立 Workspace、PermissionSession、OAuth issuer、Registry、token secret 和 Desktop Permission Broker identity。
- Cloudflare Named Tunnel / Quick Tunnel。
- Gateway 独立桌面页面，支持 Member 增删、Workspace、Path、权限模式和最终 MCP URL。
- “OAuth 授权”页面统一管理 Direct Server 与 Gateway Member。
- 公网 E2E 自检验证 Path 是否真正落到正确 Workspace Runtime，并校验 OAuth metadata / MCP auth challenge。

## 仍然保留的 Direct Server 规则

Direct Server 模式下，一个 Public Hostname 只能属于一个 Server Profile。不能通过给两个 Direct Profile 配置相同 hostname、不同 Path 来获得本地分流。

只有 Gateway 模式才允许：

```text
一个 hostname
+ 一个本地监听端口
+ 多个 Path
+ 多个独立 Profile Runtime
```

## 验收重点

Gateway 发布前至少验证：

1. `/company` 与 `/home` route probe 返回不同且正确的 Workspace fingerprint。
2. 每个 Profile 的 OAuth issuer 与 `/path` 完全一致。
3. RFC 9728 resource 为 `/path/mcp`。
4. 不同 Profile 不共享 OAuth Registry、token secret 或 PermissionSession。
5. Named Tunnel 公网 E2E 自检失败只告警，不关闭健康 Gateway。
6. OAuth Client 管理能够区分 Direct Server 与 Gateway Member。


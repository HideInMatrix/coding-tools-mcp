# coding-tools-mcp

**订阅用户最大额度使用服务**

这是一个专为订阅用户设计的 MCP 服务网关，提供最大化使用额度的安全访问解决方案。通过 Cloudflare Tunnel + 统一授权管理，帮助订阅用户高效利用平台额度。

## 项目本质

本项目的核心价值在于**为订阅用户最大化使用额度**：

- 通过统一授权和额度管理，防止单个用户超量使用
- Cloudflare Tunnel 提供稳定公网接入
- OAuth 2.0 认证体系保障额度安全
- 智能配额追踪与限流机制
- 专为订阅制服务设计

## 主要功能

- **最大化额度使用**：统一管理订阅用户的访问额度
- **Cloudflare Tunnel**：提供稳定公网接入（无需端口转发）
- **OAuth 认证**：CODING_TOOLS_MCP_OAUTH_CLIENT_ID + SECRET + PASSWORD
- **Workspace 支持**：指定独立工作空间
- **端口管理**：自动检查端口可用性
- **智能限流**：基于订阅级别配额
- **License 生成**：GPV3 专业版许可证

## 架构设计

```
[订阅用户] ── OAuth ── [coding-tools-mcp 服务器] ── Cloudflare Tunnel ── 公网
```

所有订阅用户通过本服务统一接入，额度由平台统一管理。

## 部署要求

### 基础环境
- Python 3.8+
- cloudflared（Cloudflare CLI）
- coding-tools-mcp（服务端程序）

### 环境变量（`.env`）

```env
CODING_TOOLS_MCP_OAUTH_CLIENT_ID=your_client_id
CODING_TOOLS_MCP_OAUTH_CLIENT_SECRET=your_client_secret
CODING_TOOLS_MCP_OAUTH_PASSWORD=your_oauth_password
```

### 启动方式

```bash
# 启动服务（推荐）
python start.py

# 或者直接运行
python start.py . --host 127.0.0.1 --port 8234
```

## 使用指南

### 订阅用户流程

1. 订阅平台（获得 Client ID / Secret）
2. 配置 `.env` 文件
3. 运行 `python start.py`
4. 访问 `http://your-tunnel-url/mcp` 进行额度使用

### 额度管理

- 本服务会自动记录每个订阅用户的使用量
- 超出配额时会自动限流
- 支持 GPV3 专业版许可证管理

## 许可证

本项目采用 GPV3 专业版许可证。

```
License Key: {serial}-{checksum}
Valid Until: {valid_until}
Project: coding-tools-mcp
Type: Professional
```

## 技术栈

- Python 3
- Cloudflare Tunnel
- OAuth 2.0
- Socket 端口检查
- Graceful shutdown

## 贡献指南

欢迎贡献改进：

1. Fork 本仓库
2. 创建功能分支
3. 提交 Pull Request

## 许可证

MIT License - 详见 LICENSE 文件
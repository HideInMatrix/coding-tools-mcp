# Coding Tools MCP

把本地代码目录通过 MCP 暴露给支持 Remote MCP 的客户端，并使用 OAuth 2.1 进行授权。

项目同时提供：

- PySide6 桌面版
- CLI 启动方式
- Cloudflare Quick Tunnel 测试模式
- Cloudflare Named Tunnel 固定域名模式
- macOS / Windows / Linux 桌面打包支持

推荐日常使用 **Cloudflare Named Tunnel + 固定域名**。这样 MCP 地址不会随着程序重启发生变化。

## 快速开始：固定域名模式

如果只想先跑通固定域名，按下面 5 步操作：

1. Cloudflare `Networking -> Tunnels` 创建一个 Named Tunnel。
2. Tunnel 的 `Routes -> Add route -> Published application` 添加 `mcp.example.com -> http://localhost:8234`。
3. Tunnel `Overview -> Add a replica`，复制安装命令中的 `eyJ...` Tunnel Token。
4. 打开桌面程序，填写 Workspace、OAuth 参数、`Public URL = https://mcp.example.com` 和 Tunnel Token。
5. 启动后，在 MCP Client 中添加 `https://mcp.example.com/mcp`。

参数速查：

| 参数 | 固定域名模式怎么填 | 是否来自 Cloudflare |
|------|--------------------|---------------------|
| Workspace | 你的代码项目目录 | 否 |
| Client ID | 自定义固定值，例如 `coding-tools-desktop` | 否 |
| Client Secret | 自己随机生成的强 Secret | 否 |
| Password | OAuth 授权页登录密码 | 否 |
| Public URL | `https://mcp.example.com` | 域名在 Cloudflare 中配置 |
| Tunnel Token | `Add a replica` 命令里的 `eyJ...` | 是 |
| MCP Client URL | `https://mcp.example.com/mcp` | 由 Public URL 派生 |

---

## 1. 工作原理

固定域名模式下的访问链路：

```text
MCP Client
    ↓
https://mcp.example.com/mcp
    ↓
Cloudflare
    ↓
Cloudflare Named Tunnel
    ↓
http://127.0.0.1:8234
    ↓
coding-tools-mcp
    ↓
你的本地 Workspace
```

OAuth 相关端点与 MCP 使用同一个固定域名：

```text
https://mcp.example.com/.well-known/oauth-authorization-server
https://mcp.example.com/.well-known/oauth-protected-resource
https://mcp.example.com/oauth/register
https://mcp.example.com/oauth/authorize
https://mcp.example.com/oauth/token
https://mcp.example.com/mcp
```

固定域名模式的关键原则是：

> OAuth 对外声明的公网 URL 与 Cloudflare 实际提供服务的公网 URL 必须完全一致。

不要把 `CODING_TOOLS_MCP_SERVER_URL` 配置成固定域名，却仍然运行随机的 Quick Tunnel。

---

## 2. 两种 Tunnel 模式

### 2.1 Quick Tunnel

适合临时测试、本地开发和第一次验证 MCP 是否可以工作。

地址类似：

```text
https://xxxxxxxx.trycloudflare.com/mcp
```

每次重新创建 Quick Tunnel，公网域名都可能变化，因此不适合希望长期保留 MCP Server 配置的场景。

桌面版中使用 Quick Tunnel：

```text
Public URL   = 留空
Tunnel Token = 留空
```

然后点击 `启动 MCP`，程序会自动显示本次生成的 Public MCP URL。

### 2.2 Named Tunnel

适合长期使用、固定 MCP URL、桌面版日常运行，以及不希望每次修改 MCP Server 地址的场景。

例如：

```text
https://mcp.example.com/mcp
```

Named Tunnel 使用 Cloudflare Tunnel Token 启动：

```bash
cloudflared tunnel run --token <TUNNEL_TOKEN>
```

桌面版已经内置这一启动逻辑，不需要手动运行该命令。

---

## 3. 创建 Cloudflare Named Tunnel

Cloudflare 官方文档：

- Tunnel setup: https://developers.cloudflare.com/tunnel/setup/
- Tunnel token: https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/

登录 Cloudflare Dashboard，进入：

```text
Networking
    ↓
Tunnels
```

点击 `Create Tunnel`，选择 Cloudflared 类型，并给 Tunnel 起一个名字，例如：

```text
coding-tools-mcp
```

创建完成即可。

桌面版本身已经包含 `cloudflared`，因此通常不需要把 Cloudflare 页面提供的系统服务安装命令真正安装到系统中。

---

## 4. 获取 Cloudflare Named Tunnel Token

这是固定域名模式最重要的参数之一。

进入：

```text
Cloudflare Dashboard
    ↓
Networking
    ↓
Tunnels
    ↓
选择刚才创建的 Tunnel
    ↓
Overview
    ↓
Add a replica
```

Cloudflare 会显示类似下面的安装命令：

```bash
sudo cloudflared service install eyJhIjoi......非常长的一串......
```

或者：

```bash
cloudflared tunnel run --token eyJhIjoi......非常长的一串......
```

其中这一整段：

```text
eyJ......
```

就是 **Cloudflare Named Tunnel Token**。

桌面版中的 `Tunnel Token` 就填写这个值。

### Token 安全提示

Tunnel Token 相当于这个 Tunnel 的运行凭据。拿到 Token 的人可以运行该 Tunnel，因此：

- 不要提交到 Git
- 不要写进 README
- 不要截图公开
- 不要发送到公开聊天群
- 不要写入前端代码

如果 Token 泄露，应立即在 Cloudflare 中重新生成或轮换 Tunnel Token。

---

## 5. 给 Tunnel 配置固定域名

创建 Tunnel 后进入：

```text
Networking
    ↓
Tunnels
    ↓
选择 Tunnel
    ↓
Routes
    ↓
Add route
    ↓
Published application
```

假设需要使用：

```text
mcp.example.com
```

Hostname 填写：

```text
Subdomain: mcp
Domain: example.com
```

最终得到：

```text
mcp.example.com
```

本项目桌面端默认监听：

```text
127.0.0.1:8234
```

因此 Service URL 填写：

```text
http://localhost:8234
```

或者：

```text
http://127.0.0.1:8234
```

推荐使用：

```text
http://localhost:8234
```

保存 Published Application。

最终映射关系应为：

```text
https://mcp.example.com
        ↓
Cloudflare Tunnel
        ↓
http://localhost:8234
```

公网访问使用 HTTPS，而本机 Tunnel 到 MCP 服务可以使用 HTTP，因为这段连接只发生在本机 `cloudflared -> localhost`。

---

## 6. 桌面版参数说明

开发环境启动：

```bash
python desktop.py
```

### 6.1 Workspace

填写需要 MCP 操作的代码目录。

例如 macOS：

```text
/Users/muyi/Documents/Project/my-project
```

Windows：

```text
D:\Project\my-project
```

Linux：

```text
/home/muyi/project/my-project
```

MCP 的文件读取、搜索、代码修改和命令执行都会限制在这个 Workspace 中。

不要直接选择整个用户主目录或磁盘根目录。

### 6.2 Client ID

这里的 Client ID 是：

```text
coding-tools-mcp 自己的 OAuth 预注册 Client ID
```

它不是 Cloudflare Client ID，也不需要去 Cloudflare Dashboard 获取。

可以自己设置一个固定值，例如：

```text
coding-tools-desktop
```

也可以随机生成：

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

建议生成后长期保持不变。

### 6.3 Client Secret

这是与上面的 Client ID 配套的 OAuth Secret，同样不是 Cloudflare 的 Secret。

建议随机生成：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

不要使用 `123456`、`password`、`admin` 等弱密码。

当前 `coding-tools-mcp` 同时支持 RFC 7591 Dynamic Client Registration。支持 DCR 的 MCP Client 会通过：

```text
/oauth/register
```

动态注册自己的 Client，因此桌面界面里的 Client ID / Client Secret 主要作为服务端预注册 OAuth Client 配置保留。

### 6.4 Password

这是 OAuth 授权页面使用的登录密码。

当 MCP Client 第一次要求授权时，浏览器会打开类似：

```text
https://mcp.example.com/oauth/authorize
```

授权页面要求输入 Password。

建议生成强密码：

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

这个密码应长期保持不变。

### 6.5 Public URL

这是 MCP 对外使用的固定基础域名。

固定域名模式填写：

```text
https://mcp.example.com
```

推荐不要填写：

```text
https://mcp.example.com/mcp
```

虽然程序会规范化末尾 `/mcp`，但推荐始终填写基础 URL。

程序最终显示的 MCP 地址会是：

```text
https://mcp.example.com/mcp
```

如果使用 Quick Tunnel：

```text
Public URL = 留空
```

### 6.6 Tunnel Token

只有固定域名模式需要填写。

填写 Cloudflare Tunnel `Add a replica` 页面安装命令中的：

```text
eyJ......
```

固定域名示例：

```text
Public URL:
https://mcp.example.com

Tunnel Token:
eyJhIjoi......
```

Quick Tunnel 模式：

```text
Public URL   = 留空
Tunnel Token = 留空
```

固定域名模式必须同时填写：

```text
Public URL
Tunnel Token
```

程序会阻止只有固定 URL、却没有 Named Tunnel Token 的错误配置。

### 6.7 保存 Secret

桌面端可以保存：

```text
Client Secret
Password
Tunnel Token
```

当前保存在用户配置目录。

macOS：

```text
~/Library/Application Support/Coding Tools MCP/settings.json
```

Windows：

```text
%APPDATA%\Coding Tools MCP\settings.json
```

Linux：

```text
~/.config/coding-tools-mcp/settings.json
```

macOS / Linux 会把配置文件权限限制为当前用户可读写。

后续版本可以进一步接入 macOS Keychain、Windows Credential Manager 或 Linux Secret Service，提高 Secret 存储安全性。

---

## 7. 推荐的固定域名配置示例

假设：

```text
域名        = mcp.example.com
本地端口    = 8234
Workspace   = /Users/muyi/Documents/Project/demo
```

Cloudflare Tunnel Route：

```text
Hostname:
mcp.example.com

Service:
http://localhost:8234
```

桌面端：

```text
Workspace:
/Users/muyi/Documents/Project/demo

Client ID:
coding-tools-desktop

Client Secret:
<随机生成的 Secret>

Password:
<随机生成的 OAuth 登录密码>

Public URL:
https://mcp.example.com

Tunnel Token:
eyJ......
```

启动成功后应该显示：

```text
URL Mode:
Named Tunnel

Public MCP URL:
https://mcp.example.com/mcp
```

之后在 MCP Client 中始终使用：

```text
https://mcp.example.com/mcp
```

无需再使用 `trycloudflare.com` 地址。

---

## 8. `.env` 配置

CLI 模式可以使用项目根目录中的 `.env`。

参考：

```bash
cp .env.example .env
```

固定域名模式示例：

```dotenv
CODING_TOOLS_MCP_OAUTH_CLIENT_ID="coding-tools-desktop"
CODING_TOOLS_MCP_OAUTH_CLIENT_SECRET="请替换为随机 Secret"
CODING_TOOLS_MCP_OAUTH_PASSWORD="请替换为随机登录密码"
CODING_TOOLS_MCP_SERVER_URL="https://mcp.example.com"
CODING_TOOLS_MCP_TUNNEL_TOKEN="eyJ......"
```

Quick Tunnel 模式：

```dotenv
CODING_TOOLS_MCP_OAUTH_CLIENT_ID="coding-tools-desktop"
CODING_TOOLS_MCP_OAUTH_CLIENT_SECRET="请替换为随机 Secret"
CODING_TOOLS_MCP_OAUTH_PASSWORD="请替换为随机登录密码"
CODING_TOOLS_MCP_SERVER_URL=""
CODING_TOOLS_MCP_TUNNEL_TOKEN=""
```

不要提交 `.env`，它应始终存在于 `.gitignore` 中。

---

## 9. CLI 使用方式

新的模块化 CLI：

```bash
python -m coding_tools_launcher.cli /path/to/workspace
```

它会读取 `.env`，并根据以下参数自动选择模式：

```text
CODING_TOOLS_MCP_SERVER_URL
CODING_TOOLS_MCP_TUNNEL_TOKEN
```

两者为空：

```text
Quick Tunnel
```

两者都有值：

```text
Named Tunnel
```

项目根目录的 `start.py` 当前保留为原始 Quick Tunnel 兼容启动脚本，用于已经验证可工作的旧流程。

如果需要固定域名，请优先使用桌面版或者：

```bash
python -m coding_tools_launcher.cli /path/to/workspace
```

---

## 10. MCP Client 中应该填写什么地址

固定域名：

```text
https://mcp.example.com/mcp
```

不是：

```text
https://mcp.example.com
```

也不是：

```text
http://localhost:8234/mcp
```

本地地址只供 Cloudflare Tunnel 转发使用。

---

## 11. 验证固定域名

启动桌面 MCP 后，可以先检查 OAuth metadata：

```bash
curl -i https://mcp.example.com/.well-known/oauth-authorization-server
```

正常应该返回 JSON，并包含：

```json
{
  "issuer": "https://mcp.example.com",
  "authorization_endpoint": "https://mcp.example.com/oauth/authorize",
  "token_endpoint": "https://mcp.example.com/oauth/token",
  "registration_endpoint": "https://mcp.example.com/oauth/register"
}
```

再测试 MCP 未授权响应：

```bash
curl -i https://mcp.example.com/mcp
```

没有 Access Token 时出现 `401 Unauthorized` 是正常的。

重点是响应应该来自 `coding-tools-mcp`，而不是 Cloudflare Access 登录页、Cloudflare 403 页面或 WAF Block 页面。

---

## 12. 测试 Dynamic Client Registration

OAuth DCR 地址：

```text
https://mcp.example.com/oauth/register
```

测试：

```bash
curl -i \
  -X POST \
  https://mcp.example.com/oauth/register \
  -H 'Content-Type: application/json' \
  --data '{
    "client_name": "mcp-test",
    "redirect_uris": ["https://example.com/callback"],
    "grant_types": ["authorization_code"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none"
  }'
```

正常成功状态：

```text
HTTP 201
```

如果这里返回 `HTTP 403`，优先检查 Cloudflare，而不是 MCP Python 代码。

---

## 13. `Dynamic client registration failed: 403`

如果 MCP Client 添加服务器时出现：

```text
Dynamic client registration failed:
registration endpoint returned 403
```

重点检查 `/oauth/register` 请求有没有真正到达 `coding-tools-mcp`。

`coding-tools-mcp 0.3.0` 的 DCR 正常成功响应为：

```text
201 Created
```

客户端元数据错误通常是 `400`，因此公网固定域名出现 `403` 时，应首先排查 Cloudflare 安全层。

### 13.1 Cloudflare Access

不要在 MCP 域名前面直接放一个要求浏览器登录的 Cloudflare Access 页面。

MCP Client 调用：

```text
/.well-known/*
/oauth/register
/oauth/token
/mcp
```

时无法像普通用户一样处理 Cloudflare Access 的网页登录流程。

### 13.2 WAF

进入 Cloudflare：

```text
Security
    ↓
Events
```

查看 `/oauth/register` 的 POST 是否被某条规则 Block。

如果确认是误拦截，应针对 `mcp.example.com` 和必要的 OAuth / MCP 路径做精确规则调整。

不建议为了 MCP 直接关闭整个域名的所有 WAF 防护。

### 13.3 自定义 Firewall Rule

检查有没有类似：

```text
Block POST
Block non-browser User-Agent
Block Bot
只允许特定国家
只允许自己的 IP
```

这些规则都可能导致 MCP Client 的服务器到服务器请求被拦截。

---

## 14. OAuth 状态跨重启持久化

固定 Named Tunnel 解决的是 MCP URL 不变化，例如长期保持：

```text
https://mcp.example.com/mcp
```

`coding-tools-mcp 0.3.x` 本身的动态 OAuth Client Registry 是进程内存状态，默认情况下 MCP Server 退出后动态注册的 `client_id` 会丢失。

本项目启动器已经额外实现持久化层，启动时会按公网 MCP 基础 URL 保存两类状态：

- RFC 7591 Dynamic Client Registration 生成的 OAuth Client 信息
- `CODING_TOOLS_MCP_OAUTH_TOKEN_SECRET`

因此在固定 Named Tunnel 模式下，例如长期使用：

```text
https://mcp.example.com/mcp
```

正常完成一次动态注册后，再停止并重新启动桌面 MCP，原来的 `client_id` 仍然存在，已有 access token 的签名 Secret 也不会因为重启而改变。

macOS 默认保存目录：

```text
~/Library/Application Support/Coding Tools MCP/oauth/
```

Windows 默认保存目录：

```text
%APPDATA%\Coding Tools MCP\oauth\
```

Linux 默认保存目录：

```text
~/.config/coding-tools-mcp/oauth/
```

每个公网 MCP 基础 URL 使用独立的状态文件，避免不同服务器之间复用 OAuth Client Registry。

### 从旧版本升级

如果旧版本已经停止过 MCP，并且客户端当前提示：

```text
Unknown client_id
```

说明旧的动态注册信息已经在升级前丢失，无法从服务端自动恢复。升级到包含持久化功能的版本后，需要让 MCP Client 重新完成一次 Dynamic Client Registration / OAuth 授权。

这一次重新注册成功后，后续再停止和启动 MCP 时就不需要重复删除、重建连接器。

这是和 Cloudflare Named Tunnel 不同的第二层问题。

---

## 15. 开发环境安装

安装桌面依赖：

```bash
pip install -r requirements-desktop.txt
```

启动桌面端：

```bash
python desktop.py
```

---

## 16. cloudflared

正式桌面安装包必须包含当前平台对应的 `cloudflared`。

构建前执行：

```bash
python scripts/fetch_cloudflared.py
```

二进制会下载到：

```text
vendor/cloudflared/<platform>/cloudflared
```

开发环境如果没有 bundled `cloudflared`，会回退到系统 PATH。

打包后的桌面程序不会依赖最终用户预先安装 `cloudflared`。

---

## 17. 构建桌面应用

```bash
python build_desktop.py
```

macOS 会生成：

```text
Coding Tools MCP.app
```

Windows / Linux 应在对应平台分别构建。

---

## 18. 生成 Release 分发文件

```bash
python scripts/package_release.py --version 1.0.0
```

生成文件：

| 平台 | Release 文件 |
|------|--------------|
| Windows x64 | `Coding-Tools-MCP-<version>-windows-x64.zip` |
| Windows ARM64 | `Coding-Tools-MCP-<version>-windows-arm64.zip` |
| macOS Intel | `Coding-Tools-MCP-<version>-macos-intel.dmg` |
| macOS Apple Silicon | `Coding-Tools-MCP-<version>-macos-apple-silicon.dmg` |
| Linux x64 | `Coding-Tools-MCP-<version>-linux-x64.tar.gz` |
| Linux ARM64 | `Coding-Tools-MCP-<version>-linux-arm64.tar.gz` |

每个分发文件旁边会同时生成对应的 `.sha256` 校验文件。

---

## 19. GitHub Actions 自动打包

Workflow：

```text
.github/workflows/desktop-build.yml
```

支持：

1. GitHub Actions 页面手动运行
2. 推送 `v*` 标签后自动生成 Release

例如：

```bash
git tag v1.0.0
git push origin v1.0.0
```

工作流会在不同原生架构 Runner 上分别构建对应安装包。

---

## 20. 安全建议

请至少遵守以下原则：

1. Workspace 只选择需要操作的项目目录。
2. OAuth Password 使用随机强密码。
3. Tunnel Token 不进入 Git。
4. `.env` 不进入 Git。
5. MCP 域名不要暴露其他无关服务。
6. 不要直接关闭整个 Cloudflare WAF。
7. 如果出现 403，先检查 Cloudflare Security Events。
8. 固定域名推荐使用独立子域名，例如 `mcp.example.com`。

---

## 21. License

本项目使用 AGPL-3.0 License，详细条款请查看：

```text
LICENSE
```

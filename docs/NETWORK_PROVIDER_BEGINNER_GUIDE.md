# 网络提供商安装与部署教程（新手版）

这份教程面向第一次使用 Coding Tools MCP 的用户。

你不需要先理解 MCP、反向代理或内网穿透的全部原理，只需要记住一件事：

```text
Coding Tools MCP 在你的电脑上运行
        ↓
第一个 Server 默认是 http://127.0.0.1:8234/mcp
        ↓
网络方案负责把这个本地地址安全地提供成公网 HTTPS 地址
        ↓
ChatGPT / AI 客户端访问公网 MCP 地址
```

`8234` 现在只是默认端口。桌面端可以创建多个 MCP Server，例如使用 `8234`、`8235`、`8236`。下面教程中的 `8234` 示例都应替换为当前 Server Profile 实际配置的端口。

> 建议第一次使用时优先选择 **Cloudflare Quick Tunnel**。它配置最少，适合先验证 Workspace、OAuth 和 MCP 是否能正常工作。

---

## 1. 开始前需要准备什么

无论使用哪一种网络方案，都先准备：

1. 一个你希望 AI 访问的项目目录，也就是 `Workspace`。
2. 一个 OAuth 登录密码，也就是桌面端的 `Password`。
3. 选择一种网络方案。

桌面端不提供 Client ID / Client Secret 手动配置。AI / MCP 客户端通过 `/oauth/register` 使用 Dynamic Client Registration 自动创建 OAuth Client，并使用服务端返回的 `client_id` 继续授权流程。

---

## 2. 网络方案怎么选

| 方案 | 推荐人群 | 是否需要账号 | 是否需要 VPS | 上手难度 |
|---|---|---:|---:|---:|
| Cloudflare Quick Tunnel | 第一次体验、临时使用 | 否 | 否 | 最简单 |
| Cloudflare Named Tunnel | 需要固定域名 | Cloudflare | 否 | 简单 |
| FRP | 有自己的 VPS、希望完全自建 | 否 | 是 | 中等 |
| ngrok | 已经使用 ngrok 服务 | ngrok | 否 | 简单 |
| Tailscale Funnel | 已有 Tailscale 网络 | Tailscale | 否 | 中等 |
| 自定义公网 URL | 已有 Nginx/Caddy/SSH Tunnel 等方案 | 取决于你的方案 | 取决于你的方案 | 高级 |

如果你不知道选哪个：

```text
第一次使用
→ Cloudflare Quick Tunnel

想要固定域名
→ Cloudflare Named Tunnel

已经有 VPS
→ FRP

已经有 ngrok 账号
→ ngrok

已经在用 Tailscale
→ Tailscale Funnel
```

---

## 3. Cloudflare Quick Tunnel

这是最适合新手验证功能的方案。

### 3.1 桌面端怎么填

选择：

```text
网络方案：Cloudflare Tunnel
```

然后：

```text
Public URL   = 留空
Tunnel Token = 留空
```

填写：

```text
Workspace
Password
```

点击：

```text
启动 MCP
```

程序会自动启动内置的 `cloudflared`，然后得到类似：

```text
https://xxxxxxxx.trycloudflare.com/mcp
```

把这个地址添加到 ChatGPT / MCP Client 即可。

### 3.2 注意事项

Quick Tunnel 的域名不是固定的。

关闭并重新创建 Tunnel 后，地址可能变化，所以它适合测试，不适合长期固定使用。

---

## 4. Cloudflare Named Tunnel（固定域名）

如果你希望长期使用：

```text
https://mcp.example.com/mcp
```

推荐使用 Named Tunnel。

Cloudflare 当前官方入口：

- Tunnel 文档：<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/>
- Dashboard 创建 Tunnel：<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/>

### 4.1 创建 Tunnel

登录 Cloudflare Dashboard，进入：

```text
Networking
→ Tunnels
→ Create a tunnel
```

名称可以填写：

```text
coding-tools-mcp
```

### 4.2 添加公网 Hostname

给 Tunnel 添加 Published Application / Route。

例如：

```text
Hostname:
mcp.example.com

Service:
http://127.0.0.1:8234
```

也就是：

```text
https://mcp.example.com
        ↓
Cloudflare Tunnel
        ↓
http://127.0.0.1:8234
```

### 4.3 获取 Tunnel Token

进入 Tunnel 的连接器安装页面，复制安装命令中：

```text
--token 后面的 eyJ... 内容
```

注意：

```text
Tunnel Token ≠ Connector ID
Tunnel Token ≠ OAuth Client ID
```

### 4.4 桌面端填写

```text
网络方案：Cloudflare Tunnel

Public URL:
https://mcp.example.com

Tunnel Token:
eyJ......
```

然后点击 `启动 MCP`。

最终 MCP Client 地址：

```text
https://mcp.example.com/mcp
```

---

## 5. FRP：使用自己的 VPS

FRP 适合已经有一台公网 VPS 的用户。

FRP 官方文档：

- 官网：<https://gofrp.org/>
- Client 配置：<https://gofrp.org/en/docs/reference/client-configures/>
- HTTP 自定义域名示例：<https://gofrp.org/en/docs/examples/vhost-http/>

FRP 分成：

```text
frps = 服务端，运行在 VPS
frpc = 客户端，运行在你的电脑
```

结构：

```text
ChatGPT
   ↓
https://mcp.example.com
   ↓
你的 VPS
   ↓
frps
   ↓
frpc
   ↓
http://127.0.0.1:8234
```

### 5.1 VPS 安装 frps

从 FRP 官方 Release 获取对应服务器平台的 FRP。

FRP GitHub：

<https://github.com/fatedier/frp/releases>

例如目录中会有：

```text
frps
frpc
frps.toml
frpc.toml
```

VPS 使用 `frps`。

### 5.2 一个基础 frps.toml 示例

```toml
bindPort = 7000
vhostHTTPPort = 8080

auth.method = "token"
auth.token = "请替换成你自己的随机 Token"
```

启动：

```bash
./frps -c ./frps.toml
```

服务器防火墙至少需要根据你的实际方案放行：

```text
7000  frpc 连接 frps
8080  HTTP vhost（如果直接使用）
```

如果使用 Nginx/Caddy 做 HTTPS，通常还需要：

```text
80
443
```

### 5.3 本机 frpc.toml

例如：

```toml
serverAddr = "你的 VPS IP 或域名"
serverPort = 7000

auth.method = "token"
auth.token = "与 frps 相同的 Token"

[[proxies]]
name = "coding-tools-mcp"
type = "http"
localIP = "127.0.0.1"
localPort = 8234
customDomains = ["mcp.example.com"]
```

### 5.4 DNS

把：

```text
mcp.example.com
```

解析到你的 VPS。

### 5.5 HTTPS

推荐在 VPS 使用：

```text
Nginx
或
Caddy
```

负责：

```text
HTTPS :443
   ↓
FRP HTTP vhost :8080
```

这样最终给 ChatGPT 的地址仍然是：

```text
https://mcp.example.com/mcp
```

### 5.6 Coding Tools MCP 桌面端

选择：

```text
网络方案：FRP
```

填写：

```text
Public URL:
https://mcp.example.com

FRP Config:
/path/to/frpc.toml
```

`frpc` 客户端区域可以：

```text
点击“自动检测”
```

程序会依次尝试：

```text
应用内置客户端
→ 标准安装目录
→ PATH
```

如果检测不到，再点击：

```text
选择…
```

手动选择 `frpc`。

当前版本不会自动生成你的 `frpc.toml`，请先按上面的示例准备好配置文件。

---

## 6. ngrok

ngrok 适合不想维护 VPS，同时愿意使用第三方 Tunnel 服务的用户。

官方文档：

- ngrok Agent：<https://ngrok.com/docs/agent>
- ngrok CLI：<https://ngrok.com/docs/agent/cli>

### 6.1 安装 ngrok

按照 ngrok 官方下载页面安装 Agent，并登录你的 ngrok 账号。

安装后可以先验证：

```bash
ngrok version
```

### 6.2 配置 Auth Token

ngrok 官方推荐使用：

```bash
ngrok config add-authtoken <YOUR_TOKEN>
```

这样 token 会进入 ngrok 自己的配置文件。

Coding Tools MCP 中也可以直接填写：

```text
Auth Token
```

二选一即可。

如果你已经在 ngrok Agent 中配置好了 token，桌面端 Auth Token 可以留空。

### 6.3 桌面端配置

选择：

```text
网络方案：ngrok
```

点击：

```text
自动检测
```

如果检测成功，会显示：

```text
版本
来源
实际路径
```

如果没有固定域名：

```text
Public URL = 留空
```

程序会启动类似：

```text
ngrok http http://127.0.0.1:8234
```

并自动识别生成的 HTTPS URL。

如果你的 ngrok 账号已经配置固定 Endpoint，也可以填写 Public URL。

---

## 7. Tailscale Funnel

Tailscale Funnel 适合已经使用 Tailscale 的用户。

官方文档：

- 安装：<https://tailscale.com/docs/install>
- Funnel：<https://tailscale.com/kb/1223/funnel>

### 7.1 先安装并登录 Tailscale

安装完成后，需要登录到你的 tailnet。

Linux 常见流程：

```bash
sudo tailscale up
```

macOS / Windows 通常通过官方桌面客户端登录。

### 7.2 Funnel 的基本要求

Tailscale Funnel 需要满足 Tailscale 当前要求，例如：

```text
Tailscale 客户端可用
已经登录 tailnet
MagicDNS / HTTPS 条件满足
tailnet 允许 Funnel
```

首次启用 Funnel 时可能会打开浏览器，让你授权开启 Funnel。

### 7.3 桌面端配置

选择：

```text
网络方案：Tailscale Funnel
```

点击：

```text
自动检测
```

程序不仅检查 `tailscale` 文件是否存在，还会执行受控的状态验证。

所以：

```text
找到 tailscale
```

并不等于：

```text
Tailscale 已经可以正常使用
```

如果后台服务没有运行、没有登录或者 wrapper 已损坏，界面会给出提示。

启动 MCP 后，程序会尝试把：

```text
http://127.0.0.1:8234
```

通过 Funnel 提供成：

```text
https://<设备>.<tailnet>.ts.net/mcp
```

---

## 8. 自定义公网 URL

如果你已经有自己的网络方案，例如：

```text
Nginx
Caddy
SSH Reverse Tunnel
已有 FRP
其他网关
```

可以选择：

```text
网络方案：自定义公网 URL
```

然后只填写：

```text
Public URL:
https://mcp.example.com
```

Coding Tools MCP 不会启动额外 Tunnel 进程。

你只需要保证：

```text
https://mcp.example.com
        ↓
http://127.0.0.1:8234
```

能够正常转发。

---

## 9. “自动检测客户端”是什么意思

FRP、ngrok、Tailscale 页面都提供自动检测。

程序不会扫描整块硬盘。

检测顺序：

```text
应用内置客户端
→ 系统标准安装目录
→ PATH
```

找到候选后还会执行安全验证，例如：

```text
frpc --version
ngrok version
tailscale version
```

验证使用参数数组执行，不使用 shell，并设置短超时。

如果你点击 `选择…` 手动指定客户端，手动路径优先级最高。

---

## 10. 怎么判断已经部署成功

启动成功后，桌面端状态应该是：

```text
● Running
```

并显示：

```text
Public MCP URL
```

例如：

```text
https://mcp.example.com/mcp
```

然后在 ChatGPT / MCP Client 中添加这个地址。

第一次授权时会进入 OAuth 页面，输入你在桌面端设置的：

```text
Password
```

授权完成后，客户端应能看到 Coding Tools MCP 暴露的工具。

---

## 11. 常见问题

### 11.1 显示 Stopped

先看桌面端运行日志。

常见原因：

```text
8234 端口被占用
网络客户端没有安装
Token 配置错误
FRP 配置文件错误
公网域名没有正确解析
```

### 11.2 自动检测不到 frpc / ngrok

点击：

```text
选择…
```

手动选择实际可执行文件。

### 11.3 Tailscale 找到了路径，但仍提示不可用

这通常说明：

```text
Tailscale 后台服务没有运行
尚未登录
CLI wrapper 指向失效文件
Funnel 权限未开启
```

先在终端验证：

```bash
tailscale version
tailscale status
```

### 11.4 FRP 已连接，但公网访问失败

检查：

```text
DNS 是否指向 VPS
frps 是否监听正确端口
VPS 防火墙是否放行
Nginx/Caddy 是否正确转发
frpc customDomains 是否与 Public URL 域名一致
```

### 11.5 OAuth 登录成功，但 MCP Client 连接失败

固定域名模式必须保证：

```text
Public URL
OAuth Metadata URL
MCP Client 实际访问 URL
```

使用的是同一个公网域名。

---

## 12. 安全建议

不要把 Workspace 直接选成：

```text
/
C:\
整个用户主目录
```

推荐只选择：

```text
具体项目目录
```

例如：

```text
/Users/me/Projects/my-app
```

同时：

1. OAuth Password 使用随机强密码。
2. Tunnel Token、FRP Token、ngrok Auth Token 不要提交到 Git。
3. 不要把这些 Token 发到公开聊天、Issue 或截图中。
4. FRP 服务端只开放必要端口。
5. 固定域名使用 HTTPS。

---

## 13. 下一步

如果你已经跑通网络连接，可以继续阅读：

- [网络 Provider 架构说明](./NETWORK_PROVIDERS.md)
- [MCP Server 开发文档](./MCP_SERVER_DEVELOPMENT.md)
- [返回 README](../README.md)

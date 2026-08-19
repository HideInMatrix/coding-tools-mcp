# 网络提供方案

Coding Tools MCP 的核心职责是把指定 Workspace 通过受控 MCP 工具提供给 AI。

公网连接只是传输层，因此桌面启动器把它抽象为独立的 `NetworkProvider`：

```text
Workspace
    ↓
coding-tools-mcp
    ↓
http://127.0.0.1:8234/mcp
    ↓
NetworkProvider
    ↓
公网 HTTPS MCP URL
```

当前支持：

```text
Cloudflare Tunnel
FRP
ngrok
Tailscale Funnel
自定义公网 URL
```

## 1. 模块结构

```text
coding_tools_launcher/network/
├── __init__.py
├── base.py
├── process.py
├── factory.py
├── cloudflare.py
├── frp.py
├── ngrok.py
├── tailscale.py
└── external.py
```

Provider 使用的外部客户端统一由另一个模块发现和验证：

```text
coding_tools_launcher/executables/
├── models.py
├── specs.py
├── discovery.py
├── verification.py
└── resolver.py
```

统一解析顺序为：

```text
用户手动指定
    ↓
应用内置客户端
    ↓
标准安装目录
    ↓
系统 PATH
```

自动检测不会递归扫描用户目录或整个磁盘，也不申请 Full Disk Access、root 或管理员权限。

发现候选后会先解析真实路径、检查普通文件/执行权限，再通过参数数组运行短时间版本探测。探测过程不使用 shell，因此路径中的空格或 shell 元字符不会进入命令解析。

`base.py` 定义统一接口：

```python
class NetworkProvider:
    def start(self, host, port, config): ...
    def stop(self): ...
    @property
    def is_running(self): ...
```

所有基于子进程的 Provider 共用 `ProcessNetworkProvider`，统一处理：

```text
可执行文件解析/验证
子进程启动
stdout/stderr 转发
启动稳定性检查
动态公网 URL 等待
退出码
停止/清理
```

因此新增网络方案时不需要重复实现进程生命周期代码。

## 2. Cloudflare Tunnel

Cloudflare 保留两种模式。

### Quick Tunnel

界面：

```text
网络方案 = Cloudflare Tunnel
Public URL = 留空
Tunnel Token = 留空
```

程序自动创建 `trycloudflare.com` HTTPS 地址。

### Named Tunnel

单电脑固定域名仍可直接使用：

```text
网络方案 = Cloudflare Tunnel
Public URL = https://mcp.example.com
Tunnel Token = eyJ...
```

Cloudflare Published Application 应指向本机 MCP：

```text
http://127.0.0.1:8234
```

多电脑固定入口采用更直接的模型：**每台电脑一个独立 hostname + 一个独立 Named Tunnel + 一个独立 Tunnel Token**。

例如：

```text
https://company.mcp.example.com/mcp -> 公司电脑独立 Tunnel
https://home.mcp.example.com/mcp    -> 家里电脑独立 Tunnel
```

Cloudflare 中每个 hostname 的 Published Application 都直接指向对应电脑的 `http://127.0.0.1:<MCP端口>`，Path 保持为空，不需要 Worker 或 Path Router。

桌面端仍保留 URL Path 能力，例如 `https://company.mcp.example.com/crm/mcp`。该能力不是用来选择不同 Cloudflare Tunnel，而是为后续 **Local MCP Gateway** 预留：未来同一台机器可由一个 Gateway 端口按 `/crm`、`/project-a` 等 Path 分发到多个本地 Server Profile。当前直连模式下，同一个 Public Hostname 只允许配置一个 Profile。

Cloudflare 当前仍是唯一默认随桌面包一起分发 helper 二进制的 Provider。

FRP/ngrok/Tailscale 已经支持同一套“内置客户端优先”逻辑；后续只要对应二进制被加入 `vendor/<product>/<platform>/`，Provider 无需修改即可自动使用。

## 3. FRP

FRP 模式假设你已经有自己的 `frps` 服务端和 HTTPS 域名。

桌面端只负责启动本机 `frpc`：

```text
网络方案 = FRP
Public URL = https://mcp.example.com
frpc = 可选；支持自动检测或手动选择
FRP Config = /path/to/frpc.toml
```

界面中的“自动检测”按以下顺序寻找 `frpc`：应用内置版本、标准安装目录、系统 PATH。找到后展示版本、来源和真实路径；自动检测到的 bundle/PATH 路径不会写入用户配置，避免把临时打包目录固化到 `settings.json`。

推荐让 `frpc` 配置只处理隧道，HTTPS 证书和域名由 VPS 上的 Nginx/Caddy/frps vhost 层管理。

例如公网链路：

```text
ChatGPT
   ↓
https://mcp.example.com
   ↓
VPS Nginx/Caddy
   ↓
frps
   ↓
frpc
   ↓
127.0.0.1:8234
```

FRP Provider 不解析或重写用户的配置文件，因此可以继续使用你已有的 FRP 配置体系。

## 4. ngrok

```text
网络方案 = ngrok
Public URL = 可选
ngrok = 自动检测或手动选择
Auth Token = 可选
```

客户端检测成功后会显示类似：

```text
✓ ngrok 3.x.x · 标准安装目录 · /opt/homebrew/bin/ngrok
```

如果 Public URL 留空，程序运行：

```text
ngrok http http://127.0.0.1:8234 --log stdout --log-format json
```

并从 ngrok 输出中自动识别动态 HTTPS URL。

如果填写 Public URL，则会通过 `--url` 请求固定 Endpoint。

如果机器已经通过 ngrok 配置文件保存了凭据，界面的 Auth Token 可以留空。

## 5. Tailscale Funnel

```text
网络方案 = Tailscale Funnel
tailscale = 自动检测或手动选择
```

Tailscale 除了验证 CLI 版本，还会执行受控的 `tailscale status --json` 状态探测。如果客户端存在但 daemon 未运行、尚未登录或状态不可解析，界面会用警告状态显示，而不是把“找到 executable”等同于“Funnel 已可用”。

程序使用 HTTPS/443 Funnel，把：

```text
http://127.0.0.1:8234
```

发布为：

```text
https://<node>.<tailnet>.ts.net
```

首次使用 Funnel 时，Tailscale 可能要求浏览器批准对应 tailnet 权限。

停止 MCP 时 Provider 会同时关闭本次 HTTPS/443 Funnel。

## 6. 自定义公网 URL

这个模式完全不启动隧道子进程。

适用于：

```text
VPS + Nginx/Caddy
SSH reverse tunnel
已有的 FRP/ZeroTier/反向代理脚本
其他第三方 Tunnel
```

界面只需要填写：

```text
Public URL = https://mcp.example.com
```

你负责确保该公网地址最终转发到：

```text
http://127.0.0.1:8234
```

程序仍然会使用这个公网 URL 生成 OAuth metadata 和 `/mcp` 地址。

## 7. 配置持久化

普通网络配置会写入桌面设置文件，例如：

```json
{
  "network_provider": "frp",
  "network": {
    "frp": {
      "public_url": "https://mcp.example.com",
      "executable": "",
      "config_file": "/Users/me/frpc.toml"
    }
  }
}
```

客户端路径的保存规则：

```text
自动检测到的内置/系统客户端 -> 仅展示，不持久化路径
用户通过“选择…”指定的客户端 -> 作为 executable override 保存
```

这样应用升级或 PyInstaller bundle 目录变化时，不会因为旧的临时路径导致 Provider 无法启动。

以下字段属于敏感凭据：

```text
OAuth Password
Cloudflare Tunnel Token
ngrok Auth Token
```

只有勾选“在这台电脑上保存敏感凭据”时才会写入 `settings.json`。

## 8. 新增 Provider

新增一个网络方案通常只需要三步：

1. 在 `network/` 下创建新的 Provider 类；
2. 在 `factory.py` 注册 key；
3. 在桌面端增加对应配置页。

Launcher 不需要修改，因为它只依赖统一接口：

```text
create_network_provider()
provider.start()
provider.is_running
provider.stop()
```

这也是当前网络层模块化的主要目标。

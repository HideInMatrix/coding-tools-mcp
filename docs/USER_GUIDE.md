# MicroMatrix Workbench 使用文档

这份文档只讲如何使用 MicroMatrix Workbench。

如果你想先了解项目适合谁、解决什么问题，请查看项目根目录的 `README.md`。

如果你需要配置 Cloudflare、FRP、ngrok、Tailscale 或自定义公网地址，请查看：

[网络提供商安装与部署教程（新手版）](NETWORK_PROVIDER_BEGINNER_GUIDE.md)

## 1. 使用前准备

你需要准备：

- 一台 macOS、Windows 或 Linux 电脑
- 一个需要 AI 协助的本地项目目录
- 一个支持 Remote MCP 的 AI 客户端
- 至少一种可以让 AI 客户端访问本地 MCP 服务的网络方案

例如你的项目目录：

```text
/Users/me/Projects/my-app
```

这个目录就是后面要选择的 Workspace。

## 2. 启动桌面程序

打开 MicroMatrix Workbench 桌面程序后，主要会看到：

```text
当前服务
服务名称
本地端口
Workspace
Password
网络方案
网络方案对应的配置区域
运行状态
Public MCP URL
运行日志
```

普通用户主要只需要关注：

```text
服务名称
本地端口
Workspace
Password
网络方案
```

桌面程序现在可以保存并同时运行多个 MCP Server Profile。第一个服务默认使用 `8234`，后续新建服务会自动建议 `8235`、`8236` 等未被 Profile 占用的端口，也可以手工修改。

例如：

```text
公司项目  -> 127.0.0.1:8234
个人项目  -> 127.0.0.1:8235
临时测试  -> 127.0.0.1:8236
```

每个服务拥有独立的 `server_id`、Workspace、网络配置和 OAuth Client Registry。

## 3. 选择 Workspace

在 `Workspace` 一栏选择希望 AI 操作的项目目录。

例如：

```text
/Users/me/Projects/my-app
```

选择后，MCP 的文件读取、搜索、修改和命令操作都会以这个目录为主要边界。

建议：

- 一个项目选择一个 Workspace
- 不要直接选择整个用户主目录
- 不要选择磁盘根目录
- 启动前确认路径是否是当前真正想让 AI 操作的项目

### 3.1 选择权限模式

每个 Server Profile 都可以独立选择：

```text
请求批准（Safe，推荐）
帮我批准（Trusted）
完全访问权限（Dangerous）
```

普通开发建议保持 `Safe`。它会启用 Workspace 边界、净化后的环境变量和当前平台可用的 OS 沙箱；低风险操作直接执行，访问网络、用户工具环境、Git 元数据等越界能力时再请求批准。

`Trusted` 适合明确需要网络、较长任务和常用开发脚本的项目，这些能力会减少询问，但仍保留 Workspace 与 OS 沙箱边界；破坏性命令、Git 元数据写入和宿主机用户工具环境等能力仍按需批准。

`Dangerous` 会关闭 OS 进程沙箱并继承完整用户环境，只应在你明确需要普通终端级权限时使用。

如果 AI 在 `Safe` 模式请求执行 `git add`、`git commit`、联网命令等受限操作，优先使用 MCP 客户端自己的授权交互。批准后只对对应的工具调用临时放行，不需要把整个 Server 切换成 `Dangerous`。

如果当前客户端不支持 MCP elicitation，桌面版会自动显示 MicroMatrix Workbench 自己的本地授权框。选择“仅允许本次”后，授权会在同一次逻辑工具调用中持续累积，避免一个命令先申请 `long_timeout`、随后申请 `privileged_executable` 时来回重复弹窗。

“仅允许本次”右侧的下拉菜单还提供“本次服务会话全部允许”：它会对同一个已认证 MCP Client，在当前 MCP Server 进程停止或重启前自动放行所有可临时授权的权限。它不会关闭 Workspace 边界，也不会提升 `sandbox_env_override` 等不可临时授权的系统限制；真正的普通终端级完全访问仍然必须显式选择 `Dangerous`。

当前 MCP HTTP 传输是无状态的，因此桌面端无法可靠识别 ChatGPT 的“某一个聊天线程”。这里的“服务会话”指当前 MCP Server 运行进程 + 已认证 OAuth Client，而不是单个 ChatGPT 对话。停止或重启该 Server 后，会话级授权自动清空。

对于本地工具查找，程序先使用沙箱可见 PATH；找不到时只申请一次读取用户登录环境的权限。若在最高可用权限下仍然找不到目标程序或工具链，本次 MCP Server 会话会缓存该失败结果，不再重复弹出同样的环境权限请求。安装或调整工具环境后，重启当前 MCP Server 即可重新检测。

## 4. 设置 OAuth Password

`Password` 是 MCP OAuth 授权页面使用的登录密码。

建议使用随机且不容易猜到的密码。

桌面端不再提供 Client ID / Client Secret 手动配置。OAuth Client 必须通过 `/oauth/register` 使用 Dynamic Client Registration 创建，ChatGPT / MCP Client 每次建立新的 OAuth Client 时都由服务端生成新的 `client_id`。

## 5. Dynamic Client Registration

MicroMatrix Workbench 只支持动态 Client 注册，不支持手工预注册 Client。

客户端连接时会先读取 OAuth metadata，然后调用：

```text
POST /oauth/register
```

服务端返回新的 `client_id`，之后客户端再使用该 ID 进入 `/oauth/authorize` 完成授权。

关于 `client_id` 的生成、桌面端持久化、程序重启恢复，以及删除旧 MCP 后重新创建连接时的完整流程，参见：

```text
docs/OAUTH_CLIENT_ID_FLOW.md
```

桌面左侧的“授权”页面可以按 MCP Server 查看动态注册的 Client。Persistent Server 停止后可以撤销单个 Client 或全部 Client；Quick Tunnel 的 Client 只属于当前临时 Session，停止服务后自动销毁。

## 6. 选择网络方案

桌面端目前支持：

```text
Cloudflare Tunnel
FRP
ngrok
Tailscale Funnel
自定义公网 URL
```

如果不知道如何选择，可以参考：

| 你的情况 | 推荐方案 |
|---|---|
| 第一次测试 | Cloudflare Quick Tunnel |
| 已有 Cloudflare 域名 | Cloudflare Named Tunnel |
| 有自己的 VPS | FRP |
| 想临时快速得到公网 URL | ngrok |
| 已经使用 Tailscale | Tailscale Funnel |
| 已有 Nginx/Caddy/反向代理 | 自定义公网 URL |

如果有多台电脑，推荐为每台电脑创建独立 Named Tunnel、独立 Tunnel Token，并使用不同 Public Hostname：

```text
https://company.mcp.example.com/mcp
https://home.mcp.example.com/mcp
```

Cloudflare 中对应的 Published Application Path 保持为空即可，不需要额外配置 Worker/Path Router。桌面端的 `Local Gateway Path（预留）` 不负责跨电脑分流，它是后续同一台机器“一个 Gateway 端口承载多个 Profile”的路由标识。

完整部署步骤见：

[网络提供商安装与部署教程（新手版）](NETWORK_PROVIDER_BEGINNER_GUIDE.md)

## 7. FRP、ngrok、Tailscale 客户端检测

FRP、ngrok 和 Tailscale 页面提供客户端检测能力。

一般会看到：

```text
[自动检测] [选择…]
```

点击 `自动检测` 后，程序会尝试从应用内置客户端、标准安装目录和系统 PATH 中寻找对应程序。

程序不会递归扫描整块硬盘。

检测成功后会显示类似：

```text
已检测
版本: 3.x.x
来源: 标准安装目录
路径: /opt/homebrew/bin/ngrok
```

如果自动检测失败，可以点击 `选择…` 手动指定可执行文件。

## 8. 启动 MCP

完成 Workspace、Password 和网络配置后，点击：

```text
启动 MCP
```

程序会准备网络连接和本地 MCP 服务。

启动过程中状态会显示 `Starting`。

启动成功后显示：

```text
● Running
```

状态文字和圆点为绿色。

同时界面会显示 `Public MCP URL`，例如：

```text
https://mcp.example.com/mcp
```

或者某个网络方案自动生成的 HTTPS 地址。

## 9. 把 Public MCP URL 添加到 AI 客户端

复制桌面端显示的 `Public MCP URL`。

在支持 Remote MCP 的 AI 客户端中新增 MCP Server，并填写这个 URL。

第一次连接时，AI 客户端通常会进入 OAuth 授权流程。

浏览器出现授权页面后，输入桌面端配置的 `Password` 完成授权。

授权成功后，AI 客户端就可以看到 MicroMatrix Workbench 提供的工具。

## 10. AI 可以做什么

连接成功以后，AI 可以通过 MCP 工具协助完成：

- 读取文件
- 浏览目录
- 搜索代码
- 修改源码
- 应用 Patch
- 查看 Git Status
- 查看 Git Diff
- 查看 Git Log
- 查看 Commit 内容
- Git Blame
- 执行受控命令
- 管理运行中的命令
- 查看图片文件

AI 使用的是 MCP 工具，而不是直接获得电脑的任意磁盘访问权限。

## 11. 查看运行日志

桌面程序底部提供运行日志区域。

如果启动失败，优先查看这里。

常见日志来源包括：

```text
Agent Runtime
cloudflared
frpc
ngrok
tailscale
```

日志可以用于判断 MCP 是否启动、网络客户端是否连接、公网 URL 是否生成以及 OAuth 是否存在配置错误。

## 12. 停止 MCP

服务运行时，按钮会变成：

```text
停止 MCP
```

点击后程序会停止本地 MCP 服务，并关闭由当前 Provider 启动的网络进程。

停止成功后状态显示：

```text
● Stopped
```

状态文字和圆点为红色。

## 13. 保存配置

桌面程序会保存普通配置，例如 Workspace、当前网络方案、Public URL、FRP 配置文件路径和用户手动选择的客户端路径。

如果勾选：

```text
在这台电脑上保存敏感凭据
```

还会保存对应的 OAuth Password、Cloudflare Tunnel Token、ngrok Auth Token 等敏感字段。

如果不希望本机保存这些内容，可以关闭该选项。

## 14. CLI 使用

除了桌面版，也可以使用 CLI：

```bash
python -m agent_workbench.cli /path/to/workspace
```

CLI 会从 `.env` 读取 OAuth 和网络配置。

可以复制：

```bash
cp .env.example .env
```

普通用户优先推荐桌面版，CLI 更适合开发调试、自动化启动和远程开发环境。

## 15. 关于与检查更新

桌面程序左侧提供：

```text
服务
授权
关于
```

“服务”用于创建、保存、切换、启动和停止多个 MCP Server Profile。

“授权”用于查看当前 Server 已通过 `/oauth/register` 创建的 OAuth Client；Persistent Server 停止后可以执行撤销操作。

进入“关于”页面后，可以查看：

- 当前安装版本
- GitHub 最新 Release 版本
- 当前是否存在可用更新
- Copyright © micromatrix.org

点击 `检查版本` 会读取 GitHub 最新 Release。检测到更高版本后，按钮会变成蓝色的 `更新到 x.y.z`。

正式打包版本支持应用内更新：

```text
点击更新
  -> 应用内下载当前平台更新包
  -> 显示下载进度与已下载大小
  -> SHA-256 校验
  -> 启动独立 updater helper
  -> 当前程序退出
  -> helper 替换旧程序
  -> 自动重新启动新版本
```

如果历史 Release 缺少自动更新包或对应 `.sha256` 文件，则回退为手动打开 Release 下载页面。

macOS 使用固定 Bundle Identifier：

```text
org.micromatrix.workbench
```

当前阶段未使用付费 Developer ID / Apple Notarization，因此固定 Bundle Identifier 和应用内替换可以改善升级连续性，但不能承诺完全消除所有 Gatekeeper 提示。

Release 文件名不包含版本号，平台名称统一为：

```text
MicroMatrix-Workbench-windows-x64.exe
MicroMatrix-Workbench-windows-arm64.exe
MicroMatrix-Workbench-windows-x64.zip
MicroMatrix-Workbench-windows-arm64.zip
MicroMatrix-Workbench-macos-x64.dmg
MicroMatrix-Workbench-macos-arm64.dmg
MicroMatrix-Workbench-macos-x64.zip
MicroMatrix-Workbench-macos-arm64.zip
MicroMatrix-Workbench-linux-x64.tar.gz
MicroMatrix-Workbench-linux-arm64.tar.gz
```

Windows 的 `.exe` 是当前正式安装与应用内更新包；Windows `.zip` 仅用于旧版 `onedir` updater 迁移到单文件版本。macOS 的 `.dmg` 用于首次手动安装，`.zip` 专供应用内更新。每个可用于自动更新的包同时发布对应的 `.sha256` 校验文件。

版本号只保留在 Git Tag / GitHub Release 中，例如 `v0.1.4`，不会重复写入压缩包文件名。

## 16. 常见问题

### 启动后没有 Public MCP URL

先查看运行日志，确认网络 Provider 是否成功启动。

不同网络方案的排查方式见：

[网络提供商安装与部署教程（新手版）](NETWORK_PROVIDER_BEGINNER_GUIDE.md)

### 提示找不到 frpc、ngrok 或 tailscale

先点击对应页面的 `自动检测`。如果仍然找不到，确认客户端已经安装，再点击 `选择…` 手动指定可执行文件。

### Tailscale 明明有路径却检测失败

找到一个 `tailscale` 文件并不代表 Tailscale 当前可用。程序还会验证版本和运行状态；如果 Tailscale App 已卸载但系统中残留旧 wrapper，也会被拒绝。

### AI 无法读取 Workspace 以外的文件

这是预期行为。每个 MCP Server 都以自己的 Workspace 为访问边界。如果需要同时操作另一个项目，建议新建另一个 Server Profile，并为它分配独立端口和公网入口，而不是扩大当前 Workspace 的范围。

### 多个 MCP Server 应该怎么分配端口

第一个服务默认使用 `8234`。新建服务时可以点击“自动”选择下一个未被 Profile 使用的端口，也可以手工填写。固定 Cloudflare hostname 的 Published Application 必须指向对应服务实际配置的 `127.0.0.1:<port>`。

### 多台电脑应该怎么配置固定 MCP 地址

当前推荐每台电脑使用独立 Public Hostname、独立 Named Tunnel 和独立 Tunnel Token，例如公司电脑使用 `company.mcp.example.com`，家里电脑使用 `home.mcp.example.com`。不要依赖 `/company`、`/home` 这样的 URL Path 去选择不同 Tunnel；Cloudflare 不会自动用 HTTP Path 判断应该进入哪台电脑。

URL Path 能力仍然保留，但定位为后续 Local MCP Gateway 的路由键。Gateway 完成后，同一台机器才会支持一个公网 hostname / 一个入口端口按不同 Path 分发多个本地 Profile。

### Quick Tunnel 为什么重启后 OAuth Client 不见了

这是预期行为。Cloudflare Quick Tunnel 每次启动可能分配新的随机公网 URL，因此它使用临时 OAuth Session。停止服务时随机 URL、临时 Registry 和其中的 client_id 一起失效；重新启动后由 AI/MCP Client 再次调用 `/oauth/register`。

### Client ID 应该填什么

不需要填写，也没有手动配置入口。新的 OAuth Client 必须由 ChatGPT / MCP Client 调用 `/oauth/register` 动态注册。

## 17. 安全建议

- Workspace 只选择当前项目目录
- 不要选择 `/`、`C:\` 或整个 Home 目录
- 不要把 Token、Password、Secret 提交到 Git
- 对重要项目保持 Git 提交或备份
- 执行高影响命令前确认 AI 当前操作的 Workspace
- 公网 MCP 地址只提供给需要使用的客户端

## 18. 进一步阅读

- [网络提供商安装与部署教程（新手版）](NETWORK_PROVIDER_BEGINNER_GUIDE.md)
- [NetworkProvider 架构与开发说明](NETWORK_PROVIDERS.md)
- [MCP Server 开发文档](MCP_SERVER_DEVELOPMENT.md)

基础运行逻辑示意图：

![MicroMatrix Workbench 基础运行逻辑](assets/micromatrix-workbench-basic-flow.svg)

# MicroMatrix Workbench

MicroMatrix Workbench 是一个面向本地开发环境的 AI Agent 工作台。桌面端由 `agent_workbench` 负责交互与编排，`agent_runtime` 提供 MCP、工具、权限、安全沙箱和工作流执行等基础运行能力。

它允许你选择一个本地 Workspace，把这个目录中的代码、文件和 Git 信息通过受控的 MCP 工具提供给支持 Remote MCP 的 AI 客户端使用。

项目的重点不是把整个电脑或整个文件系统暴露出去，而是让 AI 在你明确指定的 Workspace 范围内完成读取、搜索、修改和受限命令执行等开发工作。

![](/assets/7ad1fe7dcf793a4f3d5f304a6f9c68c8.png)

## 这个项目适合谁

MicroMatrix Workbench 主要面向以下用户：

- 希望让 ChatGPT 或其他 MCP Client 直接协助本地项目开发的个人开发者
- 不希望把源码上传到第三方代码托管或在线 IDE，但又希望 AI 能理解本地代码的开发者
- 同时维护多个本地项目，希望按 Workspace 控制 AI 访问范围的用户
- 需要让 AI 读取文件、搜索代码、查看 Git、修改源码或执行受限命令的开发者
- 有自己的 VPS、FRP、ngrok、Tailscale 或其他公网接入方式，希望自由选择网络方案的高级用户
- 希望通过桌面界面完成 MCP 启动、OAuth 授权和公网连接，而不是手工维护大量命令的用户

## 它解决什么问题

### 1. AI 无法直接访问你的本地项目

普通聊天中的 AI 并不能直接读取你电脑上的项目目录。MicroMatrix Workbench 可以把你指定的 Workspace 转换成 AI 可以调用的一组 MCP 工具。

### 2. 不希望把整个本地文件系统暴露给 AI

项目以 Workspace 为边界，只允许工具在指定目录范围内工作，并对路径访问、命令执行和写入操作进行限制。

### 3. 本地 MCP 很难提供给远程 AI 客户端访问

项目支持多种网络接入方式，可以根据自己的环境选择 Cloudflare Tunnel、FRP、ngrok、Tailscale Funnel 或自定义公网 URL。

### 4. OAuth、MCP、网络工具配置分散且容易混淆

桌面端把 Workspace、OAuth 授权和网络方案集中在一个界面中管理。OAuth Client 统一通过 `/oauth/register` 使用 Dynamic Client Registration 创建，桌面端不提供手工 Client ID / Client Secret 配置。

### 5. 不同网络工具的安装路径和运行方式不统一

对于 FRP、ngrok、Tailscale 等客户端，项目提供统一的客户端检测与路径选择能力，并对发现的可执行文件进行版本验证。

## 主要能力

- 指定本地 Workspace，并限制 AI 的文件访问范围
- 通过 MCP 提供文件读取、目录浏览、代码搜索和修改能力
- 支持 Git 状态、Diff、Log、Show、Blame 等常用开发操作
- 支持受控命令执行和长时间命令管理
- 支持 OAuth 授权和 Dynamic Client Registration
- 支持 Cloudflare Tunnel
- 支持 FRP
- 支持 ngrok
- 支持 Tailscale Funnel
- 支持自定义公网 URL
- 支持 FRP、ngrok、Tailscale 客户端自动检测和手动选择
- 提供 pywebview + Vue 3.5 + TypeScript 7 桌面界面
- 前端使用 UnoCSS Tailwind v4 preset、shadcn-vue 和 Lucide Vue 图标
- 提供 CLI 启动方式
- 支持 macOS、Windows 和 Linux

## 适合的使用场景

例如，你可以把：

```text
/Users/me/Projects/my-app
```

作为 Workspace，然后让 AI 在这个项目中协助：

- 阅读和理解已有代码
- 查找某个功能或变量的实现位置
- 分析报错和调用链
- 修改代码
- 查看 Git 改动
- 检查提交历史
- 执行测试、构建或其他受限开发命令

而不需要把整个用户目录或整个磁盘开放给 AI。

## 文档

第一次使用，建议按下面顺序阅读：

1. [使用文档](docs/USER_GUIDE.md)
2. [网络提供商安装与部署教程（新手版）](docs/NETWORK_PROVIDER_BEGINNER_GUIDE.md)

如果你需要了解开发和扩展相关内容：

- [NetworkProvider 架构与开发说明](docs/NETWORK_PROVIDERS.md)
- [MCP Server 开发文档](docs/MCP_SERVER_DEVELOPMENT.md)

## 当前支持的网络方案

| 网络方案 | 适合场景 |
|---|---|
| Cloudflare Tunnel | 希望快速使用，或已经使用 Cloudflare 的用户 |
| FRP | 有自己的 VPS，希望完全控制转发链路的用户 |
| ngrok | 临时测试、快速获得公网 HTTPS 地址 |
| Tailscale Funnel | 已经使用 Tailscale 的用户 |
| 自定义公网 URL | 已经有 Nginx、Caddy、SSH Tunnel 或其他反向代理方案的用户 |

不同网络方案的具体安装和配置步骤不在 README 展开，请查看：

[网络提供商安装与部署教程（新手版）](docs/NETWORK_PROVIDER_BEGINNER_GUIDE.md)

## 桌面端开发

桌面展示层使用 `pywebview + Vue 3.5 + TypeScript 7 + Vite + UnoCSS + shadcn-vue`。

开发/构建桌面端需要：

```text
Python >= 3.10
Node.js + npm
```

首次构建前端：

```bash
cd agent_workbench/web
npm install
npm run build
```

之后可以从项目根目录启动桌面端：

```bash
python desktop.py
```

正式打包使用：

```bash
python build_desktop.py
```

`build_desktop.py` 会先构建 Vue 静态资源，再执行 PyInstaller。Node.js 只用于开发和打包，最终用户运行安装包时不需要 Node.js。

## 安全说明

MicroMatrix Workbench 的目标是提供一个受控的本地开发入口，而不是一个任意文件共享服务。

使用时仍建议：

- 只选择实际需要 AI 操作的项目目录作为 Workspace
- 不要把用户主目录或磁盘根目录直接作为 Workspace
- 妥善保存 OAuth Password、Tunnel Token、ngrok Auth Token 等敏感凭据
- 在执行修改和命令操作前确认当前 Workspace 是否正确
- 对生产环境项目保持正常的 Git 提交和备份习惯

## License

本项目采用仓库中 `LICENSE` 文件声明的许可证。

# pywebview + Vue 桌面 UI 重构

## 1. 目标

将原来的 PySide6 Widget 展示层替换为：

```text
Python Core
    ↓
DesktopAPI
    ↓
pywebview JS Bridge
    ↓
Vue 3.5 + TypeScript 7 + Vite
    ↓
UnoCSS presetWind4 + shadcn-vue + @lucide/vue
```

核心要求不是“把 Qt 控件改成 HTML”，而是让展示层与业务层彻底解耦。

## 2. Python 保留范围

- `ServerProfileStore`
- `MCPServerManager`
- `MCPLauncher`
- `OAuthClientStore`
- `NetworkProvider`
- OAuth persistence
- update checker

新增 `DesktopAPI` 作为 UI 唯一入口。

## 3. Vue 展示范围

- 服务列表
- 新建/编辑/删除 MCP Server
- 默认 8234 与自定义端口
- Cloudflare / FRP / ngrok / Tailscale / External 配置
- Workspace 系统目录选择
- 服务启动/停止
- Public MCP URL
- 运行日志
- OAuth Client 列表
- OAuth Client 撤销/全部撤销
- About/检查更新

## 4. Bridge 规则

```text
Vue Component
    ↓
src/api/desktop.ts
    ↓
window.pywebview.api
    ↓
DesktopAPI
    ↓
Manager / Store / Service
```

禁止 Vue 直接读 JSON、操作 subprocess、读写 OAuth Registry 或访问 MCPLauncher 私有属性。

## 5. 静态资源与构建

```text
agent_workbench/web
    ↓ npm run build
agent_workbench/web/dist
    ↓
build_desktop.py
    ↓
PyInstaller
```

Node.js 仅用于开发/打包，最终用户运行安装包时不需要 Node.js。

## 6. pywebview 生命周期

```text
desktop.py
    ↓
创建 DesktopAPI
    ↓
create_window(..., js_api=api)
    ↓
webview.start(http_server=True)
```

关闭窗口时由 Python 侧绑定 `DesktopAPI._close()`，内部执行 `MCPServerManager.stop_all()`；下划线方法不会作为 pywebview JS API 暴露，避免前端获得窗口生命周期控制能力。

## 7. Vapor 决策

当前固定 Vue 3.5 stable，不启用 Vapor。所有组件按照 Vapor-compatible 规范编写，等待 Vue 3.6 stable 后再作为独立任务迁移。

shadcn-vue 采用“源码组件”方式纳入项目。未来迁移 Vapor 时，应以 `components/ui/` 为兼容边界；依赖 Reka UI 的组件需要逐项进行 Vapor smoke，不允许假设第三方组件自动兼容。

## 8. 前端样式与组件基线

```text
TypeScript 7.x native tsc
Vue 3.5
Vite 8
UnoCSS
@unocss/preset-wind4
shadcn-vue
@lucide/vue
```

UnoCSS 是唯一 utility CSS 生成引擎，`presetWind4` 提供 Tailwind CSS v4 风格的 utility 兼容。shadcn-vue 官方仍以 Tailwind 为主要安装目标，因此本项目不重复执行 shadcn 的 Tailwind 初始化，而是维护 `components.json`、`cn()` 与已加入仓库的 UI 源码；每次通过 CLI 增加组件后都必须检查其 Wind4 兼容性。

## 9. 验收

- Python 运行路径不再 import PySide6 UI。
- `requirements-desktop.txt` 不再依赖 PySide6。
- Vue 只能通过 `desktop.ts` 访问 pywebview API。
- `npm run build` 先执行 TypeScript 7 原生 `tsc --noEmit`，再执行 compatibility `vue-tsc --noEmit`。
- Vite 已接入 UnoCSS，入口包含 `virtual:uno.css`。
- shadcn-vue `Button` 和 `@lucide/vue` 图标至少在一个正式页面中实际使用。
- PyInstaller 构建包含 `web/dist`。
- Windows/macOS/Linux 至少完成一次桌面启动 smoke。

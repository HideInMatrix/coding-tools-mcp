# Desktop Build Pipeline

## 1. 原则

Vue/Vite 生成的 `coding_tools_launcher/web/dist/` 是平台无关的静态资源。

因此 Release 构建必须拆成两个阶段：

```text
Frontend Build（一次）
  npm install --no-package-lock --no-audit --no-fund
  npm run build
        ↓
  web-dist artifact
        ↓
Desktop Package（按平台并行）
  macOS   ─┐
  Windows ─┼─ 复用同一份 web-dist
  Linux   ─┘
```

不要在每个平台的 PyInstaller Job 中重复安装 Node 依赖和重新构建 Vue。

## 2. 本地构建

前端已经构建时，桌面打包直接复用现有 `dist/`：

```bash
python build_desktop.py
```

需要重新构建前端时：

```bash
cd coding_tools_launcher/web
npm install --no-package-lock --no-audit --no-fund
cd ../..
python build_desktop.py --build-web
```

`--build-web` 使用 npm 安装前端依赖并执行 `npm run build`。

本地前端开发仍可使用 pnpm；这里只约束 Desktop Release / CI 构建链统一使用 npm。

## 3. CI Artifact 复用

CI 的 Frontend Job 先生成并上传 `web-dist`。各平台 Job 下载到任意目录后使用：

```bash
python build_desktop.py --web-dist /path/to/web-dist
```

PyInstaller 会把该目录统一打入：

```text
coding_tools_launcher/web/dist
```

所以 Python Runtime 不需要知道 CI Artifact 的真实来源路径。

## 4. 哪些内容必须按平台构建

以下资源不能跨平台复用：

- PyInstaller 生成的 Python Runtime。
- pywebview 对应的平台 WebView 运行环境。
- `cloudflared` 原生可执行文件。
- macOS `.app` / Windows `.exe` / Linux bundle。
- 后续可能加入的签名、公证、安装器资源。

Windows Release 使用 PyInstaller `--onefile`，直接发布
`Coding-Tools-MCP-windows-<arch>.exe`。应用内更新下载新的 `.exe`，在主进程退出后
由独立 PowerShell helper 原子替换当前可执行文件并重新启动；替换或重启失败时回滚旧 `.exe`。

过渡期 Release 还会额外生成同名 `.zip`。这个 ZIP 只用于兼容旧版 `onedir` updater，
内部仅包含 `Coding Tools MCP/Coding Tools MCP.exe`。新版本不会再消费该 ZIP；待旧版迁移窗口结束后可以移除。

macOS 与 Linux 继续使用目录型 bundle，因为 `.app` / Linux bundle 本身包含平台目录结构。

因此只复用 Web `dist/`，不要试图复用整个 Desktop Bundle。

## 5. Web 兼容边界

同一份 Web `dist/` 会分别运行在 WebView2、WKWebView、WebKitGTK 等引擎中。

前端代码不得按操作系统条件编译不同版本；平台差异通过 pywebview Bridge / Python DesktopAPI 处理。

如果未来需要支持明显更旧的 WebView 引擎，应统一调整 Vite/TypeScript 浏览器目标，然后重新生成一份所有平台共用的 `dist/`，而不是维护多个系统专属前端包。


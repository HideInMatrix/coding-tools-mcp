# 代码规范

## 1. Python

- 使用 `from __future__ import annotations`。
- 新数据模型优先使用 `@dataclass(slots=True)`。
- 不在 UI 中直接访问私有 `_clients`、`_process` 等内部容器。
- 文件持久化使用 UTF-8。
- OAuth/配置文件写入优先使用原子替换。
- 所有用户可控路径必须限制在预期配置目录或 Workspace 范围。

## 2. 分层

UI：

- 只负责收集输入和展示状态。
- 不直接启动 subprocess。
- 不直接写 OAuth Registry 文件。
- 桌面展示层统一使用 `pywebview + Vue + TypeScript + Vite`。
- Vue 组件不得直接访问 Python Manager/Store。
- Vue 组件不得散落调用 `window.pywebview.api`，所有桥接调用统一封装在 `web/src/api/desktop.ts`。

DesktopAPI：

- 是 JavaScript ↔ Python 的唯一公开业务边界。
- 只暴露面向 UI 的 DTO 和行为，不暴露 Manager、Store、Process 等 Python 对象。
- 参数必须在 Python 侧重新校验，不能信任前端已经完成校验。
- pywebview 暴露方法可能在不同线程执行，因此不得保存依赖 UI 线程的可变业务状态。

Manager：

- 负责多 Server 调度。
- 不负责具体 Provider 实现。

Runtime/Launcher：

- 负责单 Server 生命周期。

Store：

- 负责数据读取、校验、持久化。
- 不负责运行态进程。

## 3. ID

- `server_id` 由程序生成。
- `client_id` 只由 OAuth DCR 生成。
- 不允许用户手填任意一个 ID。

## 4. Secrets

不得在日志中输出：

- OAuth Password
- client_secret
- secret_digest
- access token
- refresh token
- authorization code
- Cloudflare Tunnel Token
- ngrok auth token

## 5. 错误处理

- 端口冲突必须给出明确 host/port。
- Profile 数据损坏必须指出文件位置。
- OAuth Registry 数据损坏不能静默清空。
- 删除操作必须是显式用户动作。

## 6. 测试命名

使用行为描述：

```text
test_new_profile_uses_first_available_default_port
test_persistent_server_reuses_same_oauth_registry_after_url_change
test_ephemeral_server_does_not_reuse_oauth_clients
test_remove_client_persists_registry
```

## 7. 修改原则

- 文件修改统一通过项目 MCP `apply_patch`。
- 不用 shell 重定向直接覆盖源码。
- 每个阶段结束运行相关 unittest。
- 未通过测试时不进入 UI 大规模改动。

## 8. pywebview 规范

- 桌面程序不再使用 PySide6 Widget 构建业务 UI。
- pywebview 只负责原生窗口、系统 WebView、文件选择和 JavaScript ↔ Python Bridge。
- 不额外启动 Flask/FastAPI 作为桌面管理 API。
- 前端静态资源通过 pywebview 内置本地 HTTP Server 加载。
- Python API 通过 `js_api` 暴露，前端必须等待 `pywebviewready` 后调用。
- 不允许把 Workspace、Token、Password 等敏感值拼进页面 URL/query string。
- 不允许前端直接访问用户配置文件或 OAuth Registry 文件。
- 原生文件/目录选择使用 pywebview `create_file_dialog`。

## 9. Vue 版本与 Vapor-compatible 规范

当前稳定基线：

```text
Vue                 3.5.40
Vite                8.0.14
@vitejs/plugin-vue  6.0.8
TypeScript          7.0.2
vue-tsc             3.3.8
UnoCSS              66.7.5
@unocss/preset-wind4 66.7.5
shadcn-vue          2.8.1
@lucide/vue         1.28.0
```

TypeScript 7.0 当前使用原生 Go 编译器作为项目正式 `tsc`。由于 TypeScript 7.0 暂不提供 Compiler API，而 `vue-tsc` 仍需要 TypeScript Compiler API，因此 `package.json` 必须按 TypeScript 官方过渡方案同时保留 `@typescript/typescript6` compatibility package：

```text
@typescript/native -> TypeScript 7.x，提供正式 tsc
typescript         -> @typescript/typescript6，仅供 vue-tsc 等 API 工具兼容
```

禁止因为 compatibility package 的存在，把业务代码或 CI 的正式 TypeScript 编译器降回 6.x。

当前版本**不启用 Vapor Mode**。所有 Vue 新代码必须保持 Vapor-compatible：

- 统一使用 Composition API。
- 统一使用 `<script setup lang="ts">`。
- 允许 `ref`、`reactive`、`computed`、`watch`、`watchEffect`、`defineProps`、`defineEmits`、`defineModel`。
- 禁止 Options API：`data`、`methods`、`computed: {}`、`mixins`、`extends`。
- 禁止业务代码依赖 `getCurrentInstance()`。
- 禁止业务代码手写 `h()`、VNode、render function 或读取 VNode 内部结构。
- 避免自定义 Directive；确有必要时必须单独验证 Vapor 接口兼容性。
- UI 组件体系统一采用 shadcn-vue，组件源码保存在仓库中，不把 shadcn-vue 当成黑盒运行时组件库。
- shadcn-vue 底层使用到 Reka UI 的组件必须封装在 `components/ui/`，业务页面不直接依赖 Reka UI 私有实现。
- 图标统一使用 `@lucide/vue`，不再用字母、Emoji 或自制文本符号代替功能图标。
- UI 基础样式优先使用 UnoCSS utility + CSS Variables；复杂全局布局允许保留少量语义 CSS。
- 状态应保留在组件/组合函数中，不依赖全局单例组件实例。

未来开启 Vapor 的必要条件：

1. Vue 3.6 发布 stable。
2. `@vitejs/plugin-vue` 对对应 stable Vapor 有正式支持。
3. 当前全部 Vue 组件通过 Vapor 编译。
4. 服务管理、OAuth Client 管理、日志、更新检查、表单完成回归测试。
5. Windows WebView2、macOS WKWebView、Linux WebKitGTK 至少完成一次打包 smoke。

未满足以上条件前，不允许把生产桌面 UI 切到 RC/Beta Vapor。

## 10. Vue 组件边界

```text
App.vue
  页面状态与顶层数据编排

components/
  纯展示与局部交互

api/desktop.ts
  唯一 pywebview Bridge 封装

types.ts
  Python DTO 对应的 TypeScript 类型

styles.css
  全局 Design Tokens 与基础布局

components/ui/
  shadcn-vue 基础组件源码

lib/utils.ts
  shadcn-vue 的 cn() 等基础工具
```

组件不得绕过 `api/desktop.ts` 直接调用 Python。

## 11. UnoCSS / Tailwind v4 兼容规范

- 样式引擎使用 UnoCSS，不同时运行 Tailwind CSS JIT/PostCSS 引擎。
- Tailwind v4 utility 兼容层统一使用官方 `@unocss/preset-wind4`。
- `uno.config.ts` 是 utility、theme token、preflight 的唯一配置入口。
- `main.ts` 必须导入 `virtual:uno.css`。
- shadcn-vue `components.json` 中 `tailwind.config` 保持空字符串，表示 Tailwind v4 风格配置；实际 utility 解析由 UnoCSS Wind4 完成。
- 从 shadcn-vue CLI 新增组件后，必须检查生成 class 是否在 `presetWind4` 支持范围内；不兼容 utility 必须在合入前替换。
- 不额外安装 `@tailwindcss/vite`，避免一个页面同时由 Tailwind 和 UnoCSS 两套引擎生成 utility。
- shadcn 主题颜色使用 CSS Variables，并映射到 `uno.config.ts` 的 semantic theme colors。
- `tailwind-merge` 只用于 shadcn `cn()` class 合并，不代表项目启用了 Tailwind CSS 编译器。

## 12. shadcn-vue / Lucide 规范

- shadcn-vue CLI 版本固定在当前稳定线，升级必须单独检查生成组件 diff。
- 新增通用 Button、Dialog、Select、Table、Tooltip 等组件优先通过 shadcn-vue 组件体系实现。
- 业务组件禁止复制另一份 Button/Input/Dialog 样式形成第二套 Design System。
- 图标从 `@lucide/vue` 按需静态导入，禁止整包动态注册。
- 如果 shadcn-vue CLI 生成了 `lucide-vue-next` import，必须在合入前统一替换为 `@lucide/vue`；项目禁止同时保留两套 Lucide Vue 包。
- 默认图标尺寸由具体组件控制，普通工具栏/导航建议 16–22px。
- 图标按钮必须保留可访问名称或可见文字，不允许只靠图形表达破坏可访问性。

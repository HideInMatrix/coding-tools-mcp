# 实施计划

## Phase 1：基础数据模型

状态：核心实现完成，等待 Python 3.10+ 环境执行完整单测

目标：

- 新增 `MCPServerProfile`。
- 新增 Profile Store。
- 自动生成 `server_id`。
- 默认端口从 8234 顺延分配。
- 增加 Profile 单元测试。

## Phase 2：OAuth 存储身份重构

状态：核心实现完成，等待完整单测

目标：

- Persistent OAuth Registry 改为绑定 `server_id`。
- 不再使用 `public_base_url` 作为长期存储 key。
- 增加 OAuth Client list/remove/clear API。
- 删除 Client 时持久化。
- 增加固定 Public URL 的旧 URL-hash OAuth Registry 非破坏迁移。

## Phase 3：多 Runtime Manager

状态：核心实现完成，等待完整单测

目标：

- 引入 `MCPServerManager`。
- 一个 manager 同时运行多个 Server。
- 每个 Server 拥有独立 MCP process/provider。
- `start/stop/stop_all/status`。
- 处理端口冲突。

## Phase 4：Quick Tunnel Session

状态：核心实现完成，等待完整单测

目标：

- Quick Tunnel 使用临时 OAuth Registry。
- 停止时清理 Session。
- 下次启动强制重新 DCR。

## Phase 5：桌面 Server 管理 UI

状态：pywebview + Vue 源码迁移已完成第一版，等待 Node + Python 3.10+ 环境构建/实机回归

技术方案调整：

- Python Manager/Store 保留。
- 新增 `DesktopAPI` 作为 JS ↔ Python 唯一桥接层。
- UI 使用 Vue 3.5 + TypeScript + Vite。
- 当前不启用 Vapor，但代码遵守 Vapor-compatible 规范。

目标：

- 首页改为 Server 列表。
- 新建/编辑 Server。
- 自定义端口。
- 启停单个 Server。
- 展示 Public MCP URL。

## Phase 6：OAuth Client 管理 UI

状态：正在迁移到 Vue 授权客户端页面

目标：

- 左侧新增“授权客户端”。
- 按 Server 查看 Client。
- 删除 Client。
- 清空 Client。

## Phase 7：迁移与稳定性

状态：进行中

目标：

- 已实现旧单 Server settings 自动创建“默认服务”。
- 已实现固定 Public URL 的旧 URL hash OAuth Registry 迁移，旧文件保留。
- Quick Tunnel 旧随机 URL Client 按设计不迁移。
- 完整测试。
- 更新最终用户文档。

## 当前验证状态

- `python3 -m compileall`：通过。
- `git diff --check`：通过。
- `package.json` / `tsconfig.json` JSON 校验：通过。
- TypeScript 主编译器已切换到 7.0.2；`vue-tsc` 通过官方 TypeScript 6 compatibility package 过渡。
- UnoCSS + `@unocss/preset-wind4` 已接入 Vite；shadcn-vue 基础 Button 与 `@lucide/vue` 已开始实际使用。
- `vue-tsc --noEmit` / `vite build`：当前公司环境无 Node/npm，待具备 Node 的构建环境执行。
- Python unittest：当前公司环境为 Python 3.9，项目要求至少 Python 3.10，待合适环境执行。

- 已通过 `python3 -m py_compile` 对新增/修改的核心、UI 和测试文件做语法检查。
- 当前开发机 `python3` 为 3.9.6，而项目使用 `@dataclass(slots=True)`，完整 unittest 需要 Python 3.10+ 环境。
- 在完整 unittest 通过前，本阶段状态不标记为最终完成。

## 实施原则

- 每个 Phase 必须先有测试，再进入下一 Phase。
- 不为了兼容旧静态 OAuth Client 而恢复手工 Client ID 配置。
- 不允许一次大改同时破坏单 Server CLI 和桌面端。
- 重构过程中尽量保留现有 `LaunchConfig` API，逐步迁移。

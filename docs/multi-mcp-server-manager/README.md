# Multi MCP Server Manager 重构文档

本目录用于保存 Coding Tools MCP 桌面端从“单 MCP Server 启动器”升级为“多 MCP Server 管理器”的需求分析、架构设计、实施计划、代码规范和验收标准。

本目录只存放研发阶段文档，不替代 `docs/` 下的最终用户手册。

## 文档索引

1. `01-REQUIREMENTS.md`：需求分析与边界
2. `02-ARCHITECTURE.md`：整体架构与模块职责
3. `03-OAUTH-LIFECYCLE.md`：OAuth Client 生命周期
4. `04-DATA-MODEL.md`：Server Profile、Runtime、OAuth 数据模型
5. `05-IMPLEMENTATION-PLAN.md`：分阶段代码实施计划
6. `06-CODE-STANDARDS.md`：本轮重构代码规范
7. `07-TEST-ACCEPTANCE.md`：测试与验收标准
8. `08-MIGRATION.md`：旧单 Server 配置迁移策略
9. `09-PYWEBVIEW-VUE-MIGRATION.md`：pywebview + Vue 展示层重构方案
10. `10-UI-DESIGN-GUIDELINES.md`：桌面端 UI 设计、按钮、导航、间距与组件统一规范

## 核心设计结论

- 一个桌面程序可以创建多个本地 MCP Server。
- 每个 MCP Server 拥有独立且长期稳定的 `server_id`。
- 端口默认从 `8234` 开始，但每个 Server 都可以单独设置。
- 同一时间运行的多个 Server 必须使用不同端口。
- 每个 Server 有独立 Workspace、Network Provider、OAuth Registry 和 Token Secret。
- 固定域名 Server 保存 OAuth Client，重启后恢复。
- Cloudflare Quick Tunnel 作为临时 Session，公网 URL 和 OAuth Client 随 Session 销毁。
- OAuth `client_id` 继续通过 `/oauth/register` 动态生成，不恢复手工 Client ID / Client Secret 配置。
- 桌面端增加 OAuth Client 管理能力，可查看和撤销当前 Server 已注册 Client。
- 桌面展示层统一迁移到 pywebview + Vue 3.5 + TypeScript + Vite。
- Vue 当前保持 VDOM stable，但代码必须遵守 Vapor-compatible 规范。

## 当前实施顺序

先完成文档，然后按下面顺序实现：

1. Server Profile 数据模型与持久化
2. 端口分配与冲突检查
3. OAuth 存储从 `public_base_url` 改为 `server_id`
4. Multi Server Manager Runtime
5. 桌面端 Server 列表与创建/编辑/启停
6. OAuth Client 管理页面
7. Quick Tunnel 临时 OAuth Session
8. 数据迁移、测试、文档同步

## 当前进度

- Server Profile / `server_id` / 默认端口分配：已实现。
- Persistent OAuth 按 `server_id` 隔离：已实现。
- Quick Tunnel 临时 OAuth Session：已实现。
- Multi Server Manager：已实现第一版。
- 桌面“服务”页面：已接入多 Server Profile 与自定义端口。
- 桌面“授权”页面：已实现 Client 查看、撤销和全部撤销。
- 旧固定 URL OAuth Registry 迁移：已实现非破坏迁移。
- pywebview + Vue 展示层：第一版源码已完成，等待 Node/Python 3.10+ 构建与实机回归。
- Python 语法检查：已通过。
- 完整 unittest / 桌面实机回归：等待 Python 3.10+ 环境执行。

# 测试与验收标准

## 1. Profile

- 新建第一个 Profile 默认端口 8234。
- 8234 已被 Profile 占用时建议 8235。
- 自定义端口可保存和恢复。
- server_id 重启后不变。
- 删除后重新创建得到不同 server_id。
- permission_mode 可保存和恢复；旧 Profile 未包含该字段时默认 `safe`。
- 启动子进程时必须显式传递 `--permission-mode`，不能只停留在桌面 JSON 配置。

## 2. 多 Server

- 8234 和 8235 可以同时运行。
- 同端口第二个 Server 启动失败。
- 停止 A 不影响 B。
- 一个 Server 子进程退出不应误停止其他 Server。

## 3. OAuth Persistent

- A 注册 client_id A1。
- 停止 A。
- 修改/变化 Public URL。
- 重新启动同一 server_id A。
- A1 仍存在于 Registry。

## 4. OAuth Client 管理

- list 返回注册 Client。
- remove 后 get(client_id) 返回 None。
- remove 后磁盘 Registry 不再包含该 Client。
- clear 后 Client 数量为 0。
- 删除不存在 Client 返回明确结果，不破坏 Registry。

## 5. Quick Tunnel

- Session 1 创建 Q1。
- 停止 Session 1。
- Session 2 不恢复 Q1。
- Session 2 创建新 Q2。

## 6. UI

- 可创建多个 Server。
- 可修改端口。
- 可单独启动/停止。
- 可看到各 Server Client 数量。
- 可进入 Client 页面撤销 Client。
- 每个 Server 可选择“请求批准（Safe）/ 帮我批准（Trusted）/ 完全访问权限（Dangerous）”，并显示对应风险说明。
- 客户端不支持 MCP elicitation 时，桌面端可显示本地授权框，包含 Server、tool、permission、原因和脱敏参数。
- 授权框支持“拒绝 / 仅允许本次 / 本次服务会话全部允许”；停止或删除 Server 后对应待授权请求与会话授权必须立即失效。

## 7. Permission Broker / 临时授权

- 支持 MCP `2026-07-28` elicitation 的客户端应优先走 `resultType=input_required` / `elicitation/create`。
- `requestState` 必须绑定 tool、完整 arguments、Workspace、认证 principal、permission、过期时间，并防止重复消费。
- 用户拒绝/取消后原工具不得执行。
- `scope=once` grant 只允许下一次完全相同的目标调用；不同参数不得复用。
- 同一次逻辑工具调用连续触发多个 permission 时，已经批准的 permission 必须沿重试链累积，不能出现 `long_timeout` 与 `privileged_executable` 交替重复询问。
- 本地 Broker 的“本次服务会话全部允许”只在当前 Runtime + OAuth principal 生命周期有效，Server 重启后必须恢复默认权限策略。
- 客户端不支持 elicitation 且桌面 Broker 可用时，可由桌面签名 Broker fallback 授权；headless/CLI 无 Broker 时必须 fail-closed。
- Broker 请求/响应必须 HMAC 校验；敏感参数只允许脱敏后进入桌面展示。
- Broker session secret/目录/Server ID 不得转发到 `exec_command` / `exec_process` 子进程，包括 Dangerous 模式。
- macOS `git_metadata_write` 临时授权只移除本次 Seatbelt `.git` write deny，不得顺带开放网络。
- macOS `network` 临时授权只开放本次网络，不得移除 `.git` write deny。
- Linux 对应行为分别是不再 `ro-bind` `.git`、或本次不使用 `--unshare-net`，其余 sandbox 规则保持不变。
- 沙箱 PATH 未找到程序后，只允许进行一次已批准的宿主机用户环境查询；宿主机仍未找到时必须缓存负结果，同一 Runtime 内不得反复申请相同 `privileged_executable` 权限。
- 工具 manager 的 shim/symlink 必须保留原调用 basename；安全检查可以解析真实 target，但 `pnpm` 不得因为链接到 manager binary 而实际变成 `manager <args>`。

## 8. 回归

必须保证：

- MCP 工具数量为 20，新增 `discover_toolchains` 与 `exec_process`。
- Windows Restricted Token + Job Object backend 至少通过 launcher smoke；诊断必须区分 process/filesystem/network isolation，不能把 Restricted Token 宣称为完整 AppContainer。
- 单 Server CLI 仍能启动。
- 所有 Network Provider 原有测试继续通过。
- Desktop About/Update 功能不受影响。
- 不恢复手工 OAuth Client ID / Secret 配置。

## 9. 观察项：前端版本号显示

当前该问题先进入观察阶段，暂不作为发布阻塞项处理。

- 现象：部分新版本构建中，原生桌面窗口标题能够正确显示应用版本号，但前端“关于”页面中的“当前版本”可能为空。
- 已确认 `v0.2.7` 的前端版本显示正常。
- 当前仓库 `master` / `origin/master` / `v0.2.7` 指向同一提交时，版本显示链路为 `current_version()` -> `DesktopAPI.bootstrap()["version"]` -> `App.vue` -> `AboutView.vue`。
- `v0.2.2` 曾修复 PyInstaller 环境下 `build-version.txt` 的读取路径；该修复在 `v0.2.7` 中仍然存在。
- 由于原生窗口标题能够得到正确版本号，因此现阶段不优先怀疑 `current_version()` 本身，后续重点观察前端 pywebview bridge 初始化时序、打包后的 Web `dist` 是否与源码一致，以及后续版本是否又将版本读取拆成独立 API 调用。
- 在新的正式版本发布前继续观察，不立即调整现有版本显示架构；如问题可稳定复现，再决定是否统一恢复为 `bootstrap().version` 作为前端初始化版本号来源。
- 发布前验收时必须同时检查：原生窗口标题、Sidebar 版本号、About 页面“当前版本”三处是否一致。

## 10. Cloudflare 多电脑统一域名 Path Routing

- 多台电脑允许共用同一个公网 hostname，例如 `mcp.example.com`。
- 每个独立 MCP 实例必须使用唯一实例 Path，例如 `/company`、`/home`。
- 每台电脑必须使用独立 Named Tunnel、Tunnel ID 和 Tunnel Token；禁止使用同一个 Token 的多个 replica 作为 MCP 实例分流方案。
- 同一台电脑上的多个 Server Profile 可以使用同一 hostname 的不同 Path，但不能使用完全相同的 canonical Public Base URL。
- `https://mcp.example.com/company` 的 MCP resource 必须是 `https://mcp.example.com/company/mcp`。
- RFC 9728 Protected Resource Metadata 必须可从 `/.well-known/oauth-protected-resource/company/mcp` 获取。
- Authorization Server Metadata 必须可从 `/.well-known/oauth-authorization-server/company` 获取，且 `issuer` 为 `https://mcp.example.com/company`。
- OAuth authorize/token/register endpoint 必须保留实例 Path：`/company/oauth/*`。
- OAuth 授权页 POST action 必须提交回带实例 Path 的 `/company/oauth/authorize`，不能退回根路径 `/oauth/authorize`。
- `company` 与 `home` 必须使用不同 OAuth issuer 存储目录、Registry 和 token secret。
- Named Tunnel 启动后 route probe 必须通过统一公网 URL 的实例 Path 回到当前 MCP 进程；404、502、503、504 或 probe 不匹配必须给出 Path Router/Tunnel Origin 诊断。
- route probe token 属于内部敏感环境变量，不得传入 `exec_command` / `exec_process` 子进程。
- Cloudflare Worker/Edge Router 必须同时路由普通实例路径和 RFC well-known path-insertion 路径，不能只配置 `/company/*` 而遗漏 `/.well-known/.../company/...`。
- 从旧 `https://mcp.example.com/mcp` 迁移到 `https://mcp.example.com/company/mcp` 后视为新的 OAuth/MCP 身份，需要在客户端重新添加或重新授权连接。

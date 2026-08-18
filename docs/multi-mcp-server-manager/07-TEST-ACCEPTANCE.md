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
- 每个 Server 可选择 Safe / Trusted / Dangerous，并显示对应风险说明。
- 客户端不支持 MCP elicitation 时，桌面端可显示本地授权框，包含 Server、tool、permission、原因和脱敏参数。
- 授权框支持“拒绝 / 仅允许本次”；停止或删除 Server 后对应待授权请求必须立即清理。

## 7. Permission Broker / 临时授权

- 支持 MCP `2026-07-28` elicitation 的客户端应优先走 `resultType=input_required` / `elicitation/create`。
- `requestState` 必须绑定 tool、完整 arguments、Workspace、认证 principal、permission、过期时间，并防止重复消费。
- 用户拒绝/取消后原工具不得执行。
- `scope=once` grant 只允许下一次完全相同的目标调用；不同参数不得复用。
- 客户端不支持 elicitation 且桌面 Broker 可用时，可由桌面签名 Broker fallback 授权；headless/CLI 无 Broker 时必须 fail-closed。
- Broker 请求/响应必须 HMAC 校验；敏感参数只允许脱敏后进入桌面展示。
- Broker session secret/目录/Server ID 不得转发到 `exec_command` / `exec_process` 子进程，包括 Dangerous 模式。
- macOS `git_metadata_write` 临时授权只移除本次 Seatbelt `.git` write deny，不得顺带开放网络。
- macOS `network` 临时授权只开放本次网络，不得移除 `.git` write deny。
- Linux 对应行为分别是不再 `ro-bind` `.git`、或本次不使用 `--unshare-net`，其余 sandbox 规则保持不变。

## 8. 回归

必须保证：

- MCP 工具数量为 20，新增 `discover_toolchains` 与 `exec_process`。
- Windows Restricted Token + Job Object backend 至少通过 launcher smoke；诊断必须区分 process/filesystem/network isolation，不能把 Restricted Token 宣称为完整 AppContainer。
- 单 Server CLI 仍能启动。
- 所有 Network Provider 原有测试继续通过。
- Desktop About/Update 功能不受影响。
- 不恢复手工 OAuth Client ID / Secret 配置。

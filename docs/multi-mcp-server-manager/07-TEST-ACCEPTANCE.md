# 测试与验收标准

## 1. Profile

- 新建第一个 Profile 默认端口 8234。
- 8234 已被 Profile 占用时建议 8235。
- 自定义端口可保存和恢复。
- server_id 重启后不变。
- 删除后重新创建得到不同 server_id。

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

## 7. 回归

必须保证：

- MCP 工具数量为 20，新增 `discover_toolchains` 与 `exec_process`。
- Windows Restricted Token + Job Object backend 至少通过 launcher smoke；诊断必须区分 process/filesystem/network isolation，不能把 Restricted Token 宣称为完整 AppContainer。
- 单 Server CLI 仍能启动。
- 所有 Network Provider 原有测试继续通过。
- Desktop About/Update 功能不受影响。
- 不恢复手工 OAuth Client ID / Secret 配置。

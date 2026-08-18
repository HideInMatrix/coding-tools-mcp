# 迁移策略

## 1. 旧配置来源

当前旧版本主要有：

- `settings.json` 中的单 Server Workspace、Password、Network 配置。
- `oauth/<public-url-hash>.clients.json`。
- `oauth/<public-url-hash>.token-secret`。

## 2. 首次升级

如果不存在 `servers.json`，但存在旧桌面 settings：

1. 自动创建一个 Server Profile。
2. 名称默认使用 `默认服务`。
3. server_id 新生成。
4. Workspace、Password、Network、Port 从旧配置迁移。
5. 不删除旧 settings，至少保留一个版本周期用于回滚。

## 3. OAuth Registry 迁移

如果旧配置拥有固定 `public_url`：

- 根据旧 URL 找到旧 hash Registry。
- 复制到新 `servers/<server_id>/oauth/`。
- 保留原文件，不直接删除。
- 同时迁移旧 `token-secret`，确保旧 client_id 对应的既有 access token 签名体系不会因为升级立即改变。
- 如果新目录中对应文件已经存在，则不覆盖，保证迁移幂等。

如果旧配置使用 Quick Tunnel：

- 不迁移旧随机 URL 的 OAuth Client。
- 新 Session 重新 DCR。

当前代码已实现上述迁移规则。

## 4. 幂等

迁移必须可重复执行而不会：

- 重复创建 Profile。
- 重复复制 Client。
- 覆盖已经存在的新 Server OAuth 数据。

## 5. 清理

旧文件清理由后续版本单独处理，不在首次重构中自动删除。

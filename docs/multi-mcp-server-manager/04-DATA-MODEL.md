# 数据模型

## 1. MCPServerProfile

建议字段：

```python
MCPServerProfile(
    server_id: str,
    name: str,
    workspace: Path,
    host: str,
    port: int,
    oauth_password: str,
    network: NetworkConfig,
    lifecycle: str,
    created_at: int,
    updated_at: int,
)
```

`lifecycle`：

```text
persistent
ephemeral
```

Quick Tunnel 默认使用 `ephemeral`。

固定 Public URL 的 Provider 默认使用 `persistent`。

## 2. Profile 存储

建议：

```text
settings_dir/
  servers.json
  servers/
    <server_id>/
      oauth/
        clients.json
        token-secret
```

`servers.json` 保存非运行态配置。

OAuth Registry 单独存储，不放入通用 settings.json。

## 3. Runtime 数据

运行状态不要直接持久化为“running=true”。

进程状态必须由当前运行实例判断。

```python
ServerRuntimeState(
    server_id,
    running,
    local_mcp_url,
    public_base_url,
    public_mcp_url,
    network_mode,
    exit_reason,
)
```

## 4. OAuth Client View

UI 可读取：

```python
OAuthClientSummary(
    client_id,
    client_name,
    redirect_uris,
    token_endpoint_auth_method,
    issued_at,
)
```

不返回：

- secret digest
- client secret
- token

## 5. server_id

创建 Server Profile 时生成：

```python
uuid.uuid4().hex
```

要求：

- 用户修改名称不会改变 server_id。
- 修改端口不会改变 server_id。
- 修改 Workspace 不会改变 server_id。
- 修改 Public URL 不会改变 server_id。
- 删除 Server 后 server_id 永久废弃，不复用。

## 6. 默认端口分配

算法：

```text
从 8234 开始
读取所有 Profile 已配置端口
找到第一个未配置端口
作为新 Profile 默认值
```

该算法只负责“建议值”。

启动时仍必须做真实 socket bind 检查。

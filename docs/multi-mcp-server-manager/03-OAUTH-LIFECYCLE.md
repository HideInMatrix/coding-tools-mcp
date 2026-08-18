# OAuth Client 生命周期

## 1. Persistent Server 正常流程

```text
启动 Server A
  -> server_id=A
  -> issuer=https://mcp.example.com
  -> 加载 issuer 对应 oauth/issuers/<hash>/clients.json
  -> 启动 OAuth Server

AI 创建连接
  -> GET OAuth metadata
  -> POST /oauth/register
  -> Server 生成 client_id=A1
  -> 写入内存 Registry
  -> 持久化 clients.json
  -> 返回 A1

AI 发起授权
  -> /oauth/authorize?client_id=A1
  -> registry.get(A1)
  -> 找到
  -> 用户输入 Password
  -> 返回 authorization code
  -> /oauth/token
```

## 2. Server 重启

```text
停止 Server A
重新启动 Server A
  -> issuer 仍然是 https://mcp.example.com
  -> 恢复 issuer 对应 clients.json
  -> A1 继续有效
```

因此 DCR Registry 应根据 Authorization Server `issuer` 恢复，而不是根据本地 `server_id` 恢复。

## 3. 删除 AI 中旧 MCP 后创建新 MCP

```text
原 Client A1
AI 删除旧连接
```

服务端不会自动收到“删除 A1”的通知，所以 A1 可能继续保留。

新建连接时：

```text
POST /oauth/register
  -> 生成 A2

Registry:
  A1
  A2
```

新连接授权时必须使用 A2。

A1 的残留不会阻止 A2 注册。

## 4. 用户主动撤销 Client

桌面端删除 A1：

```text
remove(A1)
  -> 内存 Registry 删除
  -> clients.json 删除
  -> A1 refresh token 删除
```

之后如果旧 AI 连接继续访问：

```text
/oauth/authorize?client_id=A1
  -> Unknown client_id
```

这是预期安全行为。

## 5. Quick Tunnel

Quick Tunnel Session 1：

```text
URL = aaa.trycloudflare.com
client_id = Q1
```

停止：

```text
URL 销毁
Session Registry 销毁
Q1 失效
```

再次启动：

```text
URL = bbb.trycloudflare.com
重新 /oauth/register
client_id = Q2
```

禁止把 Q1 强行迁移到新随机 issuer。

## 6. Unknown client_id 定义

异常场景：

- Persistent Server 本应恢复 Client，但 Registry 文件丢失。
- Client 已注册，但持久化失败。
- AI 使用了不是当前 Server 注册得到的 client_id。
- Server 错误加载了其他 issuer 的 Registry，或旧 server-id/URL 状态未迁移。

正常场景：

- 用户主动撤销 Client。
- Quick Tunnel Session 已结束，旧 client_id 再次访问。

## 7. 安全约束

禁止：

- authorize 阶段自动接纳未知 client_id。
- 用本地 server_id 代替 OAuth issuer 作为 DCR Credential 的身份边界。
- 将 Client Secret 明文写入 UI 日志。
- 将 access token、refresh token、authorization code 写入普通日志。

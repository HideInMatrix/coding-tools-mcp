# Coding Tools MCP 自研服务端开发文档

当前仓库已经不再依赖外部 `coding-tools-mcp` wheel，也不再直接 vendoring 原版源码。

项目保留原版对外能力作为兼容目标，但 MCP 协议、工具 Schema、OAuth、Workspace 隔离、Patch、进程管理和 HTTP Server 都由本仓库重新实现。

当前自研服务端版本：

```text
coding-tools-mcp 0.1.0
```

### 兼容基线与版本规则

`0.1.0` 是本仓库自研 `coding-tools-mcp` 的第一个版本。自研实现不沿用
第三方包的版本号，因此不能因为第三方实现已经发布到 `0.3.0` 就把本项目
首版标记为 `1.0.0`。

当前兼容基线为已经验证可正常工作的第三方：

```text
coding-tools-mcp 0.3.0
```

这里的“兼容”指 **客户端可观察行为兼容**，不是复制第三方源码。开发时需要
优先对齐以下行为：

- 18 个工具的名称、输入 Schema、公共 outputSchema 与 annotations；
- legacy `initialize`、modern MCP 请求和 JSON-RPC 错误语义；
- HTTP transport 的状态码、Content-Type、协议 Header 与 modern mirror headers；
- OAuth Authorization Code + PKCE、Dynamic Client Registration、Protected Resource Metadata；
- 文件读取、目录/搜索、Patch、命令生命周期、TTY、输出分页、Git 返回结构；
- 客户端可读取的结构化错误码、分页字段和 `next_action`。

以下能力不是 `0.1.x` 达成客户端兼容的强制前提，可以在后续版本独立实现：

- telemetry；
- Linux Landlock 内核级沙箱；
- 第三方包内部的单文件组织方式或私有实现细节。

每次发现“第三方 `0.3.0` 正常、自研版本异常”的行为差异，都必须：

1. 先确认差异是否属于客户端可观察行为；
2. 修复自研实现，而不是直接依赖或复制第三方 wheel；
3. 在 `tests/test_custom_mcp_server.py` 或对应测试文件增加回归测试；
4. 保留已经对外使用的兼容字段，除非有明确的版本升级计划。

### OAuth resource 兼容规则

为兼容 `coding-tools-mcp 0.3.0` 和已经按其行为实现的 MCP Client，本项目的
OAuth canonical resource 使用公网服务的 **base URL**：

```text
Public URL:       https://mcp.example.com
MCP Endpoint:     https://mcp.example.com/mcp
OAuth resource:   https://mcp.example.com
```

客户端在授权或 token 请求里传入 `https://mcp.example.com/mcp` 时，可以作为
同一 MCP 服务的 endpoint alias 接受，但内部必须规范化回 base URL。其他域名
或其他路径不能因为“看起来相似”而绕过 resource/audience 校验。

`CODING_TOOLS_MCP_SERVER_URL` 同样允许填写 base URL 或完整 `/mcp` URL，服务端
必须规范化，禁止产生 `.../mcp/mcp`。

Protected Resource Metadata 同时兼容：

```text
/.well-known/oauth-protected-resource
/.well-known/oauth-protected-resource/mcp
```

两者都应描述同一个 canonical OAuth resource。

## 1. 设计目标

本次重构解决两个问题：

1. 桌面发行包不再依赖运行时安装外部 `coding-tools-mcp`；
2. 不为了兼容而复制一个难维护的超大单文件实现。

兼容目标包括：

```text
18 个 Coding Tools
MCP legacy initialize
MCP 2026-07-28 modern request
tools/list
tools/call
inputSchema
outputSchema
structuredContent
isError
OAuth 2.1 Authorization Code + PKCE
RFC 7591 Dynamic Client Registration
Workspace confinement
apply_patch
长时间命令管理
Git 工具
view_image
```

内部实现则完全模块化。

## 2. 当前源码结构

```text
coding_tools_mcp/
├── __init__.py
├── __main__.py
├── errors.py
├── oauth.py
├── patching.py
├── processes.py
├── project_context.py
├── protocol.py
├── results.py
├── runtime.py
├── schemas.py
├── server.py
├── transport_stdio.py
└── workspace.py
```

这些模块都属于当前项目自己的实现。

## 3. 启动链路

桌面程序仍沿用原有入口：

```text
MainWindow
    ↓
MCPLauncher
    ├── NetworkProvider
    │      ├── Cloudflare
    │      ├── FRP
    │      ├── ngrok
    │      ├── Tailscale Funnel
    │      └── External URL
    │
    ↓
MCPServerProcess
    ↓
coding_tools_launcher.mcp_worker
    ↓
coding_tools_launcher.mcp_process.run_internal_mcp_server()
    ↓
coding_tools_mcp.server.main()
```

网络提供层与 MCP Server 本身完全解耦。Provider 只负责把本机
`http://127.0.0.1:8234` 变成公网 HTTPS 地址；MCP 协议、OAuth、Workspace
权限逻辑不需要知道当前使用的是 Cloudflare、FRP 还是其他方案。

网络层详细设计见 `docs/NETWORK_PROVIDERS.md`。

## 4. 为什么 outputSchema 必须保留

ChatGPT 安装 MCP 时会读取 `tools/list`。

每个工具目前都返回：

```json
{
  "name": "read_file",
  "title": "Read file",
  "description": "...",
  "inputSchema": {},
  "outputSchema": {},
  "annotations": {}
}
```

公共 `outputSchema` 至少明确：

```json
{
  "type": "object",
  "properties": {
    "ok": {
      "type": "boolean"
    },
    "error": {
      "type": "object"
    }
  },
  "required": ["ok"],
  "additionalProperties": true
}
```

这样模型可以直接判断：

```text
调用是否完成
是否成功
错误类型
错误是否可重试
错误详情
```

而不是只解析文本描述。

Schema 定义集中在：

```text
coding_tools_mcp/schemas.py
```

## 5. Tool Result 结构

工具调用由：

```text
coding_tools_mcp/results.py
```

统一编码成：

```json
{
  "content": [],
  "structuredContent": {
    "ok": true
  },
  "isError": false
}
```

其中：

- `content`：给模型阅读的精简文本或图片；
- `structuredContent`：满足 `outputSchema` 的机器可读数据；
- `isError`：MCP Tool Result 层面的错误状态。

错误示例：

```json
{
  "ok": false,
  "error": {
    "code": "PERMISSION_REQUIRED",
    "message": "network-looking commands are blocked in safe mode",
    "category": "permission",
    "retryable": false,
    "details": {
      "permission": "network"
    }
  }
}
```

## 6. 18 个工具

当前暴露：

```text
server_info
check_exec_environment
read_file
list_dir
list_files
search_text
apply_patch
exec_command
write_stdin
kill_command
read_output
git_status
git_diff
git_log
git_show
git_blame
request_permissions
view_image
```

工具元数据、参数 Schema 与 annotations 位于：

```text
coding_tools_mcp/schemas.py
```

真实 handler 位于：

```text
coding_tools_mcp/runtime.py
```

两边以工具名一一对应。

## 7. MCP 协议层

协议实现位于：

```text
coding_tools_mcp/protocol.py
```

支持：

```text
2026-07-28
2025-11-25
2025-06-18
```

Legacy 请求使用：

```text
initialize
notifications/initialized
tools/list
tools/call
ping
```

Modern `2026-07-28` 请求可以通过 `_meta` 携带：

```text
io.modelcontextprotocol/protocolVersion
io.modelcontextprotocol/clientCapabilities
io.modelcontextprotocol/clientInfo
```

modern 成功返回额外带：

```text
resultType = complete
_meta.io.modelcontextprotocol/serverInfo
```

`tools/list` 和 `server/discover` 使用保守缓存字段：

```text
ttlMs = 0
cacheScope = private
```

## 8. Workspace 边界

路径隔离集中在：

```text
coding_tools_mcp/workspace.py
```

任何用户路径都会转换为绝对真实路径，然后检查它仍然处于 Workspace 根目录内部。

例如：

```text
../../etc/passwd
```

会返回：

```text
PATH_OUTSIDE_WORKSPACE
```

而不是交给文件系统工具读取。

已有路径还会再次进行 `strict=True` 解析，从而防止已有符号链接把访问引到 Workspace 外部。

## 9. Patch Engine

代码：

```text
coding_tools_mcp/patching.py
```

支持：

```text
*** Begin Patch
*** Add File
*** Update File
*** Delete File
@@ 多 hunk
*** End Patch
```

流程：

```text
解析全部 section
    ↓
校验全部路径
    ↓
在内存计算更新结果
    ↓
全部通过后才开始写文件
    ↓
临时文件 + os.replace 原子替换
    ↓
中途失败则尝试回滚已经提交的文件
```

这避免修改到一半才发现后一个 hunk 无法匹配。

## 10. exec_command

进程生命周期由：

```text
coding_tools_mcp/processes.py
```

管理。

支持：

```text
command_id
stdout/stderr 有界保留
write_stdin
read_output
kill_command
timeout
进程组终止
```

每个输出流保留：

```text
前部 head
最近 tail
```

超出缓存的数据会通过 `evicted_gap_bytes` 告知客户端。

## 11. Permission Mode

当前支持：

```text
safe
trusted
dangerous
```

桌面程序默认：

```text
safe
```

safe 会拦截明显的：

```text
网络命令
shell expansion
inline script
破坏性命令
敏感环境变量
Workspace 外的重定向
Workspace 外的明显路径参数
过长 timeout
```

需要注意：

```text
exec_command 当前属于应用层 command policy，
不是 Linux Landlock/seccomp 或虚拟机级内核隔离。
```

因此不要把不可信用户直接连接到高权限桌面账号。

## 12. Git 工具

Git 由 `runtime.py` 使用参数数组直接调用，不经过 shell 拼接。

支持：

```text
git_status
git_diff
git_log
git_show
git_blame
```

客户端传入的路径先经过 Workspace 校验，然后才作为 Git pathspec 使用。

## 13. view_image

`view_image` 默认启用。

返回 MCP image content：

```json
{
  "type": "image",
  "mimeType": "image/png",
  "data": "base64..."
}
```

如果安装了 `Pillow`，会读取尺寸，并在超过尺寸或大小限制时尝试缩放。

所以桌面依赖保留：

```text
Pillow>=10.0
```

## 14. OAuth

OAuth 实现：

```text
coding_tools_mcp/oauth.py
```

支持：

```text
Authorization Code
PKCE S256
RFC 7591 Dynamic Client Registration
public client
client_secret_post
client_secret_basic
```

Access Token 使用项目自己的 HMAC 格式：

```text
ctm1.<payload>.<signature>
```

实现只使用 Python 标准库，不再依赖 PyJWT。

## 15. OAuth 持久化

桌面启动器已有：

```text
coding_tools_launcher/oauth_persistence.py
```

它会持久化：

```text
OAuth token signing secret
动态注册 OAuth Client Registry
```

因此自研 `oauth.py` 保留以下稳定接口：

```text
OAuthClient
OAuthClientRegistry
OAuthClientRegistry.register()
OAuthClientRegistry.add_preregistered()
OAuthClientRegistry.get()
OAuthClientRegistry.authenticates()
```

## 16. HTTP 路由

代码：

```text
coding_tools_mcp/server.py
```

主要路由：

```text
GET  /
POST /mcp

GET  /.well-known/oauth-protected-resource
GET  /.well-known/oauth-authorization-server

POST /oauth/register
GET  /oauth/authorize
POST /oauth/authorize
POST /oauth/token
```

没有有效 Bearer Token 访问受保护 `/mcp` 时返回：

```text
HTTP 401
WWW-Authenticate: Bearer resource_metadata="..."
```

## 17. Cloudflare Tunnel

Cloudflare Tunnel 仍由桌面 launcher 管理。

结构：

```text
ChatGPT
    ↓ HTTPS
Cloudflare Tunnel
    ↓ HTTP
127.0.0.1:8234/mcp
```

OAuth 固定公网地址通过：

```text
CODING_TOOLS_MCP_SERVER_URL
```

传入服务端，因此生成的 authorize/token/registration 地址不会误用 localhost。

## 18. 项目指令

`project_context.py` 会读取根目录下常见项目指令，例如：

```text
AGENTS.md
CLAUDE.md
.github/copilot-instructions.md
```

根指令内容会加入 MCP server instructions。

嵌套 `AGENTS.md` 只记录位置，不会错误地全局应用。

## 19. 测试

运行：

```bash
python -m unittest discover -s tests -v
```

`tests/test_custom_mcp_server.py` 当前覆盖：

```text
自研版本号
18 个工具
inputSchema/outputSchema
structuredContent/isError
legacy initialize
modern 2026-07-28
真实 HTTP /mcp 401
真实 HTTP Bearer tools/list
路径逃逸
safe 网络命令拦截
多 hunk patch
HMAC Access Token
```

`tests/test_oauth_persistence.py` 继续覆盖：

```text
动态 client 跨重启持久化
token secret 持久化
```

## 20. 新增工具

例如新增 `project_stats`。

第一步，在：

```text
coding_tools_mcp/schemas.py
```

新增 `ToolSpec` 与 inputSchema。

第二步，在：

```text
coding_tools_mcp/runtime.py
```

新增同名：

```python
def project_stats(self, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "files": 100,
    }
```

`Runtime.call_tool()` 会自动补：

```text
ok = true
```

并由 `results.py` 生成 MCP Tool Result。

如果该工具需要专门的模型文本，再为 `results.py` 增加 renderer。

## 21. 发布前检查

至少执行：

```bash
python -m compileall -q coding_tools_mcp coding_tools_launcher
python -m unittest discover -s tests -v
python scripts/verify_build_environment.py --expected-arch arm64
python build_desktop.py
```

不同平台应分别在对应架构 Runner 上构建。

## 22. 后续安全增强

当前自研版本最值得继续增强的是 `exec_command` 的操作系统级隔离。

建议顺序：

```text
Linux Landlock
Linux seccomp / namespace
macOS sandbox profile
Windows Job Object / restricted token
细粒度一次性 permission grant
命令审计日志
OAuth 登录限流 / CSRF 强化
```

这些增强可以在不改变 MCP Tool Schema 的情况下逐步加入。
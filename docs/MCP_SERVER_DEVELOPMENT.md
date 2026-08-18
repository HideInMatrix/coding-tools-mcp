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

- 20 个工具的名称、输入 Schema、公共 outputSchema 与 annotations；
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

### OAuth issuer / resource 兼容规则

当前实现将 OAuth Authorization Server 身份与 MCP Protected Resource 明确分离：

```text
Public URL:       https://mcp.example.com
MCP Endpoint:     https://mcp.example.com/mcp
OAuth issuer:     https://mcp.example.com
OAuth resource:   https://mcp.example.com/mcp
```

为兼容升级前已建立的连接，客户端在授权或 token 请求里传入旧 base URL
`https://mcp.example.com` 时暂时作为 legacy resource alias 接受，但内部统一规范化为
`https://mcp.example.com/mcp`。新 access token 使用 `iss=issuer`、`aud=resource`。
其他域名或其他路径不能因为“看起来相似”而绕过 resource/audience 校验。

`CODING_TOOLS_MCP_SERVER_URL` 同样允许填写 base URL 或完整 `/mcp` URL，服务端
必须规范化，禁止产生 `.../mcp/mcp`。

Protected Resource Metadata 同时兼容：

```text
/.well-known/oauth-protected-resource
/.well-known/oauth-protected-resource/mcp
```

两者都应描述 canonical MCP resource `https://mcp.example.com/mcp`，并通过
`authorization_servers` 指向 issuer `https://mcp.example.com`。

## 1. 设计目标

本次重构解决两个问题：

1. 桌面发行包不再依赖运行时安装外部 `coding-tools-mcp`；
2. 不为了兼容而复制一个难维护的超大单文件实现。

兼容目标包括：

```text
20 个 Coding Tools
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
mcp_tools_server/
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
mcp_tools_server.server.main()
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
mcp_tools_server/schemas.py
```

## 5. Tool Result 结构

工具调用由：

```text
mcp_tools_server/results.py
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

## 6. 20 个工具

当前暴露：

```text
server_info
check_exec_environment
discover_toolchains
read_file
list_dir
list_files
search_text
apply_patch
exec_process
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
mcp_tools_server/schemas.py
```

真实 handler 位于：

```text
mcp_tools_server/runtime.py
```

两边以工具名一一对应。

## 7. MCP 协议层

协议实现位于：

```text
mcp_tools_server/protocol.py
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
mcp_tools_server/workspace.py
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
mcp_tools_server/patching.py
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

## 10. 受控进程执行与工具链发现

`discover_toolchains` 在不执行用户 shell 启动文件的前提下发现 Node.js、Python 和 Go。

当前会读取 Workspace 内的版本提示：

```text
.nvmrc
.node-version
.python-version
.go-version
package.json engines.node（仅精确版本）
go.mod 的 go 版本
```

并检查有限、可预测的版本管理器目录，例如：

```text
nvm / fnm / volta / mise / asdf / nodenv / n / nodebrew
pyenv
goenv
```

不会执行：

```text
~/.zshrc
~/.zprofile
~/.bashrc
~/.profile
eval "$(...)"
```

也不会递归扫描整个 Home 目录。

`exec_process` 接收结构化 `program + args`，最终使用 `shell=False` 启动。对于不需要 shell 管道、重定向或条件表达式的构建命令，应优先使用它：

```json
{
  "program": "npm",
  "args": ["run", "build"]
}
```

`exec_command` 继续用于确实需要 shell 语义的命令，但 safe/trusted 模式会固定 shell 入口并应用更严格的 command policy。

进程生命周期由：

```text
mcp_tools_server/processes.py
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

## 11. Permission Mode 与沙箱

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
覆盖 HOME/PATH/TMP 等沙箱环境变量
```

safe/trusted 使用 ToolchainResolver 生成的受控 PATH，并把 HOME/TMP 指向 MCP 自己的 runtime 目录。safe 模式还为 npm/pip/go/cargo 设置离线环境提示，同时显式阻止常见联网型包管理和 VCS 命令。

进程执行采用三层边界：

```text
Application Policy
  -> Environment Sandbox
  -> OS Process Sandbox（平台支持时）
```

当前 OS backend：

```text
macOS    Seatbelt (/usr/bin/sandbox-exec)
Linux    bubblewrap (bwrap，可用时自动启用)
Windows  Restricted Token + Job Object（进程权限降级/进程树约束）
```

各 OS backend 在 Runtime 初始化时会先执行最小自检。默认 `auto` 模式下，自检失败会明确回退到 application-policy；如果要求 fail-closed，可设置：

```text
CODING_TOOLS_MCP_OS_SANDBOX=require
```

可选值：

```text
auto     自动启用；不可用或自检失败时明确回退
off      禁用 OS sandbox
require  必须成功启用，否则 Runtime 启动失败
```

safe/trusted 的 Workspace 本身可写，但 Workspace 根 `.git` 会在支持文件系统隔离的 OS sandbox backend 中叠加只读保护，避免普通构建进程直接改写 Git 元数据。

`dangerous` 模式属于显式逃生口：继承完整用户环境并绕过 OS process sandbox，不应作为“让 npm 可见”的常规解决方案。

Windows 当前的 Restricted Token backend 会通过 `CreateRestrictedToken` 删除特权、把 Administrators SID 变为 deny-only，并把子进程放入 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的 Job Object。Job 同时启用 UI restrictions，阻止跨 Job USER handles、剪贴板读写、桌面切换、显示设置、系统参数、全局 atom 和退出 Windows。为避免子进程在加入 Job 前抢跑，launcher 使用 `CREATE_SUSPENDED`，完成 Job 绑定后才恢复主线程。

launcher 优先使用 `CreateProcessAsUserW`；如果仅因 `ERROR_PRIVILEGE_NOT_HELD (1314)` 失败，再尝试 `CreateProcessWithTokenW`。两条路径都显式传入 MCP sanitized Unicode environment，不允许回退路径重新继承真实 `%USERPROFILE%/%APPDATA%`。

这属于真实的 Windows 内核级**进程权限/生命周期隔离**，但不是完整 AppContainer。因此 `check_exec_environment` 会分别返回：

```text
process_isolation=true
filesystem_isolation=false
network_isolation=false
```

文件系统与网络仍由 Workspace policy、sanitized environment 和 safe-mode 网络规则提供防御纵深。不能把 Restricted Token 宣称为与 Seatbelt/bubblewrap 等价的完整文件系统沙箱。

Windows 11 新的 `Experimental_CreateProcessInSandbox` 会被运行时探测，但当前仍为实验 API、无公开头文件并要求 FlatBuffer `SBOX` specification，因此暂不作为生产默认 backend。`check_exec_environment.sandbox.experimental_appcontainer_available` 会报告当前系统是否存在该导出，后续 API 稳定后再切换为 AppContainer/BFS 的完整文件系统与网络隔离。

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
mcp_tools_server/oauth.py
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
mcp_tools_server/server.py
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
20 个工具
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
mcp_tools_server/schemas.py
```

新增 `ToolSpec` 与 inputSchema。

第二步，在：

```text
mcp_tools_server/runtime.py
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
python -m compileall -q mcp_tools_server coding_tools_launcher
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
# Cloudflare 多电脑统一域名 Path Routing

## 1. 目标

多台电脑上的 Coding Tools MCP 可以共用一个对外域名，但每个 MCP 实例必须拥有独立身份、独立 Tunnel 和独立 OAuth 状态。

推荐模型：

```text
https://mcp.micromatrix.cf/company/mcp  -> 公司电脑
https://mcp.micromatrix.cf/home/mcp     -> 家里电脑
```

核心约束：

- 公网 hostname 可以相同。
- 每个实例使用唯一 Path，例如 `company`、`home`。
- 每台电脑创建独立 Cloudflare Named Tunnel。
- 每台电脑使用独立 Tunnel Token。
- Cloudflare Edge 使用 Path Router 将不同 Path 转发到不同 Tunnel。
- 不使用“同一个 Tunnel Token 在多台电脑启动多个 replica”来做 MCP 实例分流。

## 2. 架构

```text
                         mcp.micromatrix.cf
                                |
                        Cloudflare Worker
                         Path Router
                     /                     \
             /company/*                  /home/*
                  |                         |
      company-origin.micromatrix.cf   home-origin.micromatrix.cf
                  |                         |
            Tunnel Company              Tunnel Home
            Token Company               Token Home
                  |                         |
              公司电脑                    家里电脑
          127.0.0.1:8234              127.0.0.1:8234
```

`company-origin.micromatrix.cf` 和 `home-origin.micromatrix.cf` 是 Cloudflare 内部回源入口；用户在 ChatGPT 中只使用统一域名 `mcp.micromatrix.cf`。

## 3. Coding Tools MCP 配置

公司电脑：

```text
统一公网域名: https://mcp.micromatrix.cf
MCP 实例 Path: company
Tunnel Token: <COMPANY_TUNNEL_TOKEN>
```

程序内部的 canonical Public Base URL：

```text
https://mcp.micromatrix.cf/company
```

最终 MCP URL：

```text
https://mcp.micromatrix.cf/company/mcp
```

家里电脑：

```text
统一公网域名: https://mcp.micromatrix.cf
MCP 实例 Path: home
Tunnel Token: <HOME_TUNNEL_TOKEN>
```

最终 MCP URL：

```text
https://mcp.micromatrix.cf/home/mcp
```

## 4. 每台电脑创建独立 Tunnel

公司 Tunnel 的 Published Application：

```text
Hostname: company-origin.micromatrix.cf
Service:  http://127.0.0.1:8234
```

家里 Tunnel 的 Published Application：

```text
Hostname: home-origin.micromatrix.cf
Service:  http://127.0.0.1:8234
```

两个 Tunnel 必须拥有不同的 Tunnel ID 和 Tunnel Token。

本地端口可以都使用 `8234`，因为它们位于不同电脑上。

## 5. Cloudflare Worker Path Router

在统一公网 hostname 上配置 Worker Route：

```text
mcp.micromatrix.cf/*
```

项目内提供可直接部署的模板：

```text
deploy/cloudflare/path-router.js
deploy/cloudflare/wrangler.toml.example
```

`MCP_ROUTE_MAP` 负责声明“实例 Path -> 独立 Tunnel Published Application hostname”的映射，不包含 Tunnel Token。

Worker 需要同时识别普通实例 Path 和 OAuth `.well-known` 的 path-insertion 形式。

示例：

```javascript
const INSTANCES = {
  company: 'https://company-origin.micromatrix.cf',
  home: 'https://home-origin.micromatrix.cf',
}

function resolveInstance(pathname) {
  const direct = pathname.match(/^\/([^/]+)(?:\/|$)/)
  if (direct && INSTANCES[direct[1]]) return direct[1]

  const wellKnownPrefixes = [
    '/.well-known/oauth-protected-resource/',
    '/.well-known/oauth-authorization-server/',
    '/.well-known/openid-configuration/',
  ]

  for (const prefix of wellKnownPrefixes) {
    if (!pathname.startsWith(prefix)) continue
    const rest = pathname.slice(prefix.length)
    const instance = rest.split('/', 1)[0]
    if (instance && INSTANCES[instance]) return instance
  }

  return ''
}

export default {
  async fetch(request) {
    const incoming = new URL(request.url)
    const instance = resolveInstance(incoming.pathname)

    if (!instance) {
      return new Response('Unknown MCP instance', { status: 404 })
    }

    const upstream = new URL(INSTANCES[instance])
    upstream.pathname = incoming.pathname
    upstream.search = incoming.search

    return fetch(new Request(upstream, request))
  },
}
```

路由时应保留原始 path，不要删除 `/company` 或 `/home`。当前 MCP Server 同时兼容本地无前缀路径，但公网统一使用带实例前缀的 canonical URL。

## 6. OAuth / MCP URL 规则

以 `company` 实例为例：

```text
OAuth issuer:
https://mcp.micromatrix.cf/company

MCP resource:
https://mcp.micromatrix.cf/company/mcp

Authorization endpoint:
https://mcp.micromatrix.cf/company/oauth/authorize

Token endpoint:
https://mcp.micromatrix.cf/company/oauth/token

Registration endpoint:
https://mcp.micromatrix.cf/company/oauth/register

Protected Resource Metadata:
https://mcp.micromatrix.cf/.well-known/oauth-protected-resource/company/mcp

Authorization Server Metadata:
https://mcp.micromatrix.cf/.well-known/oauth-authorization-server/company
```

`home` 实例使用相同规则，只将 `company` 替换为 `home`。

## 7. Route Probe

Named Tunnel 启动后，桌面程序为当前 MCP 进程生成临时 route probe token，并访问：

```text
https://mcp.micromatrix.cf/company/.well-known/coding-tools-mcp-route-probe
```

只有请求真正回到当前 MCP 进程才返回成功。

该机制用于提前发现：

- Path Router 指向错误 Tunnel。
- Tunnel Published Application 指向错误本地端口。
- Public URL 返回 502 / 503 / 504。
- 同一个 Tunnel Token 被错误地复用到其他电脑。

probe token 仅存在于 MCP Server 内部环境，不会传递给 `exec_command` / `exec_process` 子进程。

## 8. 本机 Profile 冲突规则

允许：

```text
https://mcp.micromatrix.cf/company
https://mcp.micromatrix.cf/home
```

不允许两个独立 Server Profile 同时使用：

```text
https://mcp.micromatrix.cf/company
```

因为完全相同的 canonical Public Base URL 会导致 OAuth issuer、Registry 和 token audience 身份冲突。

## 9. 从旧单域名配置迁移

旧配置：

```text
https://mcp.micromatrix.cf/mcp
```

迁移到多电脑模式后：

```text
https://mcp.micromatrix.cf/company/mcp
https://mcp.micromatrix.cf/home/mcp
```

Path 改变后 OAuth issuer 和 resource 都发生变化，因此这是新的 MCP 身份。ChatGPT 中原有连接不能直接当成新 Path 的 OAuth 状态继续使用，应分别按新的 MCP URL 新建/重新授权连接。

## 10. 验收

公司实例至少验证：

```text
GET /company/mcp
-> 401，并且 WWW-Authenticate 指向：
   /.well-known/oauth-protected-resource/company/mcp

GET /.well-known/oauth-protected-resource/company/mcp
-> resource = https://mcp.micromatrix.cf/company/mcp

GET /.well-known/oauth-authorization-server/company
-> issuer = https://mcp.micromatrix.cf/company

GET /company/oauth/authorize
-> OAuth 授权页
```

`home` 实例执行同样验证。

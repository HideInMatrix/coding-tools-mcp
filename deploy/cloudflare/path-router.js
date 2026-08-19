function parseRouteMap(raw) {
  let value
  try {
    value = JSON.parse(raw || '{}')
  } catch {
    throw new Error('MCP_ROUTE_MAP must be valid JSON')
  }

  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('MCP_ROUTE_MAP must be a JSON object')
  }

  const routes = new Map()
  for (const [instance, origin] of Object.entries(value)) {
    if (!/^[A-Za-z0-9._~-]+$/.test(instance)) {
      throw new Error(`Invalid MCP instance key: ${instance}`)
    }
    const parsed = new URL(String(origin))
    if (parsed.protocol !== 'https:') {
      throw new Error(`MCP route origin must use HTTPS: ${instance}`)
    }
    parsed.pathname = parsed.pathname.replace(/\/+$/, '')
    parsed.search = ''
    parsed.hash = ''
    routes.set(instance, parsed)
  }
  return routes
}

function resolveInstance(pathname, routes) {
  const direct = pathname.match(/^\/([^/]+)(?:\/|$)/)
  if (direct && routes.has(direct[1])) return direct[1]

  const insertedWellKnownPrefixes = [
    '/.well-known/oauth-protected-resource/',
    '/.well-known/oauth-authorization-server/',
    '/.well-known/openid-configuration/',
  ]

  for (const prefix of insertedWellKnownPrefixes) {
    if (!pathname.startsWith(prefix)) continue
    const instance = pathname.slice(prefix.length).split('/', 1)[0]
    if (instance && routes.has(instance)) return instance
  }

  return ''
}

export default {
  async fetch(request, env) {
    let routes
    try {
      routes = parseRouteMap(env.MCP_ROUTE_MAP)
    } catch (error) {
      return new Response(`Cloudflare MCP router configuration error: ${error.message}`, {
        status: 500,
      })
    }

    const incoming = new URL(request.url)
    const instance = resolveInstance(incoming.pathname, routes)
    if (!instance) {
      return new Response('Unknown MCP instance', { status: 404 })
    }

    const upstream = new URL(routes.get(instance))
    upstream.pathname = incoming.pathname
    upstream.search = incoming.search

    return fetch(new Request(upstream, request))
  },
}

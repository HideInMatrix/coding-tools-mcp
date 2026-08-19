import type {
  DesktopBridge,
  GatewayDraft,
  GatewayDto,
  LogEntryDto,
  OAuthClientDto,
  PermissionRequestDto,
  ReleaseDto,
  UpdateStatusDto,
  ServerDraft,
  ServerDto,
} from '../types'

let bridgePromise: Promise<DesktopBridge> | null = null

function isBridgeReady(api: Partial<DesktopBridge> | undefined): api is DesktopBridge {
  return Boolean(
    api
      && typeof api.get_app_version === 'function'
      && typeof api.list_servers === 'function',
  )
}

function bridge(): Promise<DesktopBridge> {
  if (isBridgeReady(window.pywebview?.api)) return Promise.resolve(window.pywebview.api)
  if (bridgePromise) return bridgePromise

  bridgePromise = new Promise<DesktopBridge>((resolve) => {
    const resolveWhenReady = () => {
      const api = window.pywebview?.api
      if (isBridgeReady(api)) {
        resolve(api)
        return
      }
      window.setTimeout(resolveWhenReady, 10)
    }

    window.addEventListener('pywebviewready', resolveWhenReady, { once: true })
    resolveWhenReady()
  })
  return bridgePromise
}

export const desktopApi = {
  async appVersion(): Promise<string> {
    return (await bridge()).get_app_version()
  },
  async selectedServerId(): Promise<string> {
    return (await bridge()).get_selected_server_id()
  },
  async updateDownloadProxy(): Promise<string> {
    return (await bridge()).get_update_download_proxy()
  },
  async saveUpdateDownloadProxy(prefix: string): Promise<string> {
    return (await bridge()).save_update_download_proxy(prefix)
  },
  async listServers(): Promise<ServerDto[]> {
    return (await bridge()).list_servers()
  },
  async listGateways(): Promise<GatewayDto[]> {
    return (await bridge()).list_gateways()
  },
  async nextPort(): Promise<number> {
    return (await bridge()).get_next_port()
  },
  async selectServer(serverId: string): Promise<boolean> {
    return (await bridge()).select_server(serverId)
  },
  async createServer(payload: ServerDraft): Promise<ServerDto> {
    return (await bridge()).create_server(payload)
  },
  async updateServer(serverId: string, payload: ServerDraft): Promise<ServerDto> {
    return (await bridge()).update_server(serverId, payload)
  },
  async deleteServer(serverId: string): Promise<boolean> {
    return (await bridge()).delete_server(serverId)
  },
  async startServer(serverId: string, payload?: ServerDraft): Promise<ServerDto> {
    return (await bridge()).start_server(serverId, payload)
  },
  async stopServer(serverId: string): Promise<ServerDto> {
    return (await bridge()).stop_server(serverId)
  },
  async createGateway(payload: GatewayDraft): Promise<GatewayDto> {
    return (await bridge()).create_gateway(payload)
  },
  async updateGateway(gatewayId: string, payload: GatewayDraft): Promise<GatewayDto> {
    return (await bridge()).update_gateway(gatewayId, payload)
  },
  async deleteGateway(gatewayId: string): Promise<boolean> {
    return (await bridge()).delete_gateway(gatewayId)
  },
  async startGateway(gatewayId: string, payload?: GatewayDraft): Promise<GatewayDto> {
    return (await bridge()).start_gateway(gatewayId, payload)
  },
  async stopGateway(gatewayId: string): Promise<GatewayDto> {
    return (await bridge()).stop_gateway(gatewayId)
  },
  async listOAuthClients(serverId: string): Promise<OAuthClientDto[]> {
    return (await bridge()).list_oauth_clients(serverId)
  },
  async revokeOAuthClient(serverId: string, clientId: string): Promise<boolean> {
    return (await bridge()).revoke_oauth_client(serverId, clientId)
  },
  async revokeAllOAuthClients(serverId: string): Promise<number> {
    return (await bridge()).revoke_all_oauth_clients(serverId)
  },
  async listPermissionRequests(): Promise<PermissionRequestDto[]> {
    return (await bridge()).list_permission_requests()
  },
  async respondPermissionRequest(requestId: string, decision: 'deny' | 'once' | 'session'): Promise<boolean> {
    return (await bridge()).respond_permission_request(requestId, decision)
  },
  async logs(after = 0): Promise<{ cursor: number; entries: LogEntryDto[] }> {
    return (await bridge()).get_logs(after)
  },
  async chooseWorkspace(initial = ''): Promise<string> {
    return (await bridge()).choose_workspace(initial)
  },
  async chooseFile(initial = ''): Promise<string> {
    return (await bridge()).choose_file(initial)
  },
  async detectExecutable(product: string, configured = '') {
    return (await bridge()).detect_executable(product, configured)
  },
  async checkUpdate(): Promise<ReleaseDto> {
    return (await bridge()).check_update()
  },
  async startUpdate(): Promise<UpdateStatusDto> {
    return (await bridge()).start_update()
  },
  async updateStatus(): Promise<UpdateStatusDto> {
    return (await bridge()).update_status()
  },
  async installUpdate(): Promise<UpdateStatusDto> {
    return (await bridge()).install_update()
  },
  async openExternal(url: string): Promise<boolean> {
    return (await bridge()).open_external(url)
  },
}

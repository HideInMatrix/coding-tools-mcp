import type {
  BootstrapDto,
  DesktopBridge,
  LogEntryDto,
  OAuthClientDto,
  ReleaseDto,
  ServerDraft,
  ServerDto,
} from '../types'

let bridgePromise: Promise<DesktopBridge> | null = null

function bridge(): Promise<DesktopBridge> {
  if (window.pywebview?.api) return Promise.resolve(window.pywebview.api)
  if (bridgePromise) return bridgePromise

  bridgePromise = new Promise<DesktopBridge>((resolve) => {
    window.addEventListener(
      'pywebviewready',
      () => resolve(window.pywebview!.api),
      { once: true },
    )
  })
  return bridgePromise
}

export const desktopApi = {
  async bootstrap(): Promise<BootstrapDto> {
    return (await bridge()).bootstrap()
  },
  async listServers(): Promise<ServerDto[]> {
    return (await bridge()).list_servers()
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
  async listOAuthClients(serverId: string): Promise<OAuthClientDto[]> {
    return (await bridge()).list_oauth_clients(serverId)
  },
  async revokeOAuthClient(serverId: string, clientId: string): Promise<boolean> {
    return (await bridge()).revoke_oauth_client(serverId, clientId)
  },
  async revokeAllOAuthClients(serverId: string): Promise<number> {
    return (await bridge()).revoke_all_oauth_clients(serverId)
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
  async openExternal(url: string): Promise<boolean> {
    return (await bridge()).open_external(url)
  },
}

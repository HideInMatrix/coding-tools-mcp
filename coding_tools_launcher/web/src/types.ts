export type PageKey = 'servers' | 'clients' | 'logs' | 'about'

export interface NetworkConfigDto {
  provider: 'cloudflare' | 'frp' | 'ngrok' | 'tailscale' | 'external'
  public_url: string
  options: Record<string, string>
}

export interface ServerDto {
  server_id: string
  name: string
  workspace: string
  oauth_password: string
  has_saved_password: boolean
  host: string
  port: number
  lifecycle: 'persistent' | 'ephemeral'
  permission_mode: 'safe' | 'trusted' | 'dangerous'
  created_at: number
  updated_at: number
  network: NetworkConfigDto
  running: boolean
  public_mcp_url: string
  url_mode: string
  exit_reason: string
  oauth_client_count: number
}

export interface ServerDraft {
  name: string
  workspace: string
  oauth_password: string
  host: string
  port: number
  remember_secrets: boolean
  permission_mode: 'safe' | 'trusted' | 'dangerous'
  network: NetworkConfigDto
}

export interface OAuthClientDto {
  client_id: string
  client_name: string
  redirect_uris: string[]
  token_endpoint_auth_method: string
  issued_at: number
}

export interface BootstrapDto {
  app_name: string
  version: string
  update_download_proxy_prefix: string
  selected_server_id: string
  next_default_port: number
  servers: ServerDto[]
  network_providers: Array<{ key: string; label: string }>
}

export interface ReleaseDto {
  current_version: string
  latest_version: string
  tag_name: string
  release_url: string
  asset_name: string
  download_url: string
  update_asset_name: string
  update_download_url: string
  checksum_url: string
  update_available: boolean
}

export interface UpdateStatusDto {
  state: 'idle' | 'downloading' | 'verifying' | 'ready' | 'installing' | 'error'
  version: string
  progress: number
  downloaded_bytes: number
  total_bytes: number
  message: string
}

export interface LogEntryDto {
  id: number
  time: number
  message: string
}

export interface PermissionRequestDto {
  request_id: string
  server_id: string
  server_name: string
  tool_name: string
  permission: string
  reason: string
  arguments: Record<string, unknown> | unknown[]
  created_at: number
  expires_at: number
}

export interface DesktopBridge {
  bootstrap(): Promise<BootstrapDto>
  get_app_version(): Promise<string>
  save_update_download_proxy(prefix: string): Promise<string>
  list_servers(): Promise<ServerDto[]>
  get_next_port(): Promise<number>
  select_server(serverId: string): Promise<boolean>
  create_server(payload: ServerDraft): Promise<ServerDto>
  update_server(serverId: string, payload: ServerDraft): Promise<ServerDto>
  delete_server(serverId: string): Promise<boolean>
  start_server(serverId: string, payload?: ServerDraft): Promise<ServerDto>
  stop_server(serverId: string): Promise<ServerDto>
  list_oauth_clients(serverId: string): Promise<OAuthClientDto[]>
  revoke_oauth_client(serverId: string, clientId: string): Promise<boolean>
  revoke_all_oauth_clients(serverId: string): Promise<number>
  list_permission_requests(): Promise<PermissionRequestDto[]>
  respond_permission_request(requestId: string, approved: boolean): Promise<boolean>
  get_logs(after?: number): Promise<{ cursor: number; entries: LogEntryDto[] }>
  detect_executable(product: string, configured?: string): Promise<{ path: string; source: string; version: string }>
  choose_workspace(initial?: string): Promise<string>
  choose_file(initial?: string): Promise<string>
  check_update(): Promise<ReleaseDto>
  start_update(): Promise<UpdateStatusDto>
  update_status(): Promise<UpdateStatusDto>
  install_update(): Promise<UpdateStatusDto>
  open_external(url: string): Promise<boolean>
}

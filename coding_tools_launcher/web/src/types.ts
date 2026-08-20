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
  allow_network: boolean
  enable_view_image: boolean
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
  allow_network: boolean
  enable_view_image: boolean
  network: NetworkConfigDto
}

export interface GatewayMemberDto {
  server_id: string
  name: string
  workspace: string
  oauth_password: string
  has_saved_password: boolean
  instance_path: string
  permission_mode: 'safe' | 'trusted' | 'dangerous'
  lifecycle: 'persistent' | 'ephemeral'
  allow_network: boolean
  enable_view_image: boolean
  public_mcp_url: string
  local_mcp_url: string
  oauth_issuer: string
  oauth_client_count: number
}

export interface GatewayMemberDraft {
  server_id?: string
  name: string
  workspace: string
  oauth_password: string
  instance_path: string
  permission_mode: 'safe' | 'trusted' | 'dangerous'
  allow_network: boolean
  enable_view_image: boolean
}

export interface GatewayDto {
  gateway_id: string
  name: string
  mode: 'single' | 'multi'
  host: string
  port: number
  created_at: number
  updated_at: number
  network: NetworkConfigDto
  members: GatewayMemberDto[]
  running: boolean
  public_base_url: string
  url_mode: string
  exit_reason: string
  diagnostic: GatewayDiagnosticDto | null
}

export interface GatewayDraft {
  name: string
  mode: 'single' | 'multi'
  host: string
  port: number
  remember_secrets: boolean
  network: NetworkConfigDto
  members: GatewayMemberDraft[]
}

export interface GatewayProfileDiagnosticDto {
  server_id: string
  name: string
  instance_path: string
  ok: boolean
  checks: string[]
  errors: string[]
}

export interface GatewayDiagnosticDto {
  ok: boolean
  public_base_url: string
  checked_at: number
  profiles: GatewayProfileDiagnosticDto[]
}

export interface OAuthClientDto {
  client_id: string
  client_name: string
  redirect_uris: string[]
  token_endpoint_auth_method: string
  issued_at: number
  client_type: 'dcr' | 'cimd'
  revocable: boolean
}

export interface BootstrapDto {
  app_name: string
  version: string
  update_download_proxy_prefix: string
  selected_server_id: string
  next_default_port: number
  servers: ServerDto[]
  gateways: GatewayDto[]
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

export type WorkflowNodeKind =
  | 'prompt'
  | 'skill'
  | 'tool'
  | 'approval'
  | 'condition'
  | 'artifact'

export interface WorkbenchTargetDto {
  target_id: string
  server_id: string
  service_name: string
  profile_name: string
  workspace: string
  running: boolean
}

export interface PromptSummaryDto {
  id: string
  name: string
  description: string
  version: number
  scope: 'built-in' | 'global'
  arguments: Array<{
    name: string
    description?: string
    required?: boolean
  }>
}

export interface PromptDefinitionDto extends PromptSummaryDto {
  schema_version: number
  messages: Array<{
    role: 'user' | 'assistant'
    content: string
  }>
  source?: string
}

export interface ToolReferenceDto {
  provider: 'system' | 'mcp'
  tool_name: string
  connection_id?: string
}

export interface SkillSummaryDto {
  id: string
  name: string
  description: string
  version: number
  scope: 'built-in' | 'global'
  entry_prompt: string | null
  tool_references: ToolReferenceDto[]
  artifacts: string[]
}

export interface SkillDefinitionDto extends SkillSummaryDto {
  schema_version: number
  source?: string
  method_document: string
}

export interface PromptValidationDto {
  ok: boolean
  saved?: boolean
  prompt: PromptDefinitionDto
}

export interface SkillValidationDto {
  ok: boolean
  saved?: boolean
  skill: SkillDefinitionDto
}

export interface MCPDiscoveredToolDto {
  name: string
  description: string
  input_schema: Record<string, unknown>
}

export interface MCPConnectionSummaryDto {
  id: string
  name: string
  transport: 'stdio' | 'http'
  endpoint: string
  command: string
  enabled: boolean
  version: number
  tool_count: number
  last_discovered_at: number
  last_error: string
  scope: 'global'
}

export interface MCPConnectionDefinitionDto extends MCPConnectionSummaryDto {
  schema_version: number
  arguments: string[]
  environment: Record<string, string>
  environment_refs: Record<string, string>
  headers: Record<string, string>
  header_refs: Record<string, string>
  tools: MCPDiscoveredToolDto[]
  source?: string
}

export interface MCPConnectionValidationDto {
  ok: boolean
  saved?: boolean
  connection: MCPConnectionDefinitionDto
}

export interface EffectiveToolDto {
  provider: 'system' | 'mcp'
  tool_name: string
  description: string
  input_schema: Record<string, unknown>
  key: string
  workflow_executable: boolean
  connection_id?: string
  connection_name?: string
}

export interface MCPConnectionProbeDto {
  ok: boolean
  connection_id?: string
  connection?: MCPConnectionDefinitionDto
  tools?: MCPDiscoveredToolDto[]
  effective_tools?: EffectiveToolDto[]
  protocol_version: string
  elapsed_ms: number
  error: string
}

export interface WorkflowSummaryDto {
  id: string
  name: string
  description: string
  version: number
  scope: 'built-in' | 'global' | 'workspace'
  inputs_schema: Record<string, unknown>
  tags: string[]
  node_count: number
  edge_count: number
}

export interface WorkflowNodeDto {
  id: string
  type: WorkflowNodeKind
  name: string
  position: { x: number; y: number }
  config: Record<string, unknown>
  policy: {
    approval: 'none' | 'required'
    on_error: 'stop' | 'continue'
  }
}

export interface WorkflowEdgeDto {
  id: string
  source: string
  target: string
  condition: 'success' | 'failure' | 'approved' | 'rejected' | 'true' | 'false'
}

export interface WorkflowDefinitionDto {
  schema_version: number
  id: string
  name: string
  description: string
  version: number
  entry_node_id: string
  inputs_schema: Record<string, unknown>
  tags: string[]
  nodes: WorkflowNodeDto[]
  edges: WorkflowEdgeDto[]
  metadata: Record<string, unknown>
  scope?: 'built-in' | 'global' | 'workspace'
}

export interface WorkflowValidationDto {
  ok: boolean
  errors: Array<{ code: string; message: string; subject?: string }>
  warnings: Array<{ code: string; message: string; subject?: string }>
  workflow: WorkflowDefinitionDto
  saved?: boolean
}

export interface WorkbenchCatalogDto {
  target: WorkbenchTargetDto
  prompts: PromptSummaryDto[]
  skills: SkillSummaryDto[]
  tools: string[]
  effective_tools: EffectiveToolDto[]
  mcp_connections: MCPConnectionSummaryDto[]
  workflows: WorkflowSummaryDto[]
}

export interface CapabilityCatalogDto {
  prompts: PromptSummaryDto[]
  skills: SkillSummaryDto[]
  tools: string[]
  effective_tools: EffectiveToolDto[]
  mcp_connections: MCPConnectionSummaryDto[]
}

export interface WorkflowRunDto {
  run_id: string
  workflow_id: string
  workflow_version: number
  workflow_scope: string
  workspace: string
  status:
    | 'pending'
    | 'running'
    | 'waiting_model'
    | 'waiting_approval'
    | 'succeeded'
    | 'failed'
    | 'cancelled'
  engine_state: {
    activated: string[]
    ready: string[]
    completed: string[]
    outcomes: Record<string, string>
    outputs: Record<string, unknown>
  }
  inputs: Record<string, unknown>
  node_states: Record<string, { status?: string; outcome?: string; error?: string }>
  artifacts: Array<Record<string, unknown>>
  approvals: Array<Record<string, unknown>>
  pending_action: Record<string, unknown> | null
  error: string
  created_at: number
  updated_at: number
}

export interface WorkflowApprovalDto {
  request_id: string
  server_id: string
  server_name: string
  run_id: string
  node_id: string
  approval_id: string
  title: string
  description: string
  created_at: number
  expires_at: number
}

export interface DesktopBridge {
  get_app_version(): Promise<string>
  get_selected_server_id(): Promise<string>
  get_update_download_proxy(): Promise<string>
  save_update_download_proxy(prefix: string): Promise<string>
  list_servers(): Promise<ServerDto[]>
  list_gateways(): Promise<GatewayDto[]>
  get_next_port(): Promise<number>
  select_server(serverId: string): Promise<boolean>
  create_server(payload: ServerDraft): Promise<ServerDto>
  update_server(serverId: string, payload: ServerDraft): Promise<ServerDto>
  delete_server(serverId: string): Promise<boolean>
  start_server(serverId: string, payload?: ServerDraft): Promise<ServerDto>
  stop_server(serverId: string): Promise<ServerDto>
  create_gateway(payload: GatewayDraft): Promise<GatewayDto>
  update_gateway(gatewayId: string, payload: GatewayDraft): Promise<GatewayDto>
  promote_server_to_gateway(serverId: string, payload: GatewayDraft): Promise<GatewayDto>
  delete_gateway(gatewayId: string): Promise<boolean>
  start_gateway(gatewayId: string, payload?: GatewayDraft): Promise<GatewayDto>
  stop_gateway(gatewayId: string): Promise<GatewayDto>
  test_gateway(gatewayId: string): Promise<GatewayDiagnosticDto>
  list_oauth_clients(serverId: string): Promise<OAuthClientDto[]>
  list_gateway_oauth_clients(gatewayId: string, serverId: string): Promise<OAuthClientDto[]>
  revoke_oauth_client(serverId: string, clientId: string): Promise<boolean>
  revoke_all_oauth_clients(serverId: string): Promise<number>
  revoke_gateway_oauth_client(gatewayId: string, serverId: string, clientId: string): Promise<boolean>
  revoke_all_gateway_oauth_clients(gatewayId: string, serverId: string): Promise<number>
  list_permission_requests(): Promise<PermissionRequestDto[]>
  respond_permission_request(requestId: string, decision: 'deny' | 'once' | 'session'): Promise<boolean>
  list_workflow_approvals(): Promise<WorkflowApprovalDto[]>
  respond_workflow_approval(requestId: string, approved: boolean): Promise<boolean>
  list_workbench_targets(): Promise<WorkbenchTargetDto[]>
  get_workbench_catalog(targetId: string): Promise<WorkbenchCatalogDto>
  get_workbench_capability_catalog(): Promise<CapabilityCatalogDto>
  get_workbench_mcp_connection(connectionId: string): Promise<MCPConnectionDefinitionDto>
  validate_workbench_mcp_connection(connection: MCPConnectionDefinitionDto): Promise<MCPConnectionValidationDto>
  save_workbench_mcp_connection(connection: MCPConnectionDefinitionDto, expectedVersion: number): Promise<MCPConnectionValidationDto>
  delete_workbench_mcp_connection(connectionId: string): Promise<boolean>
  test_workbench_mcp_connection(connectionId: string, timeoutSeconds?: number): Promise<MCPConnectionProbeDto>
  discover_workbench_mcp_connection_tools(connectionId: string, timeoutSeconds?: number): Promise<MCPConnectionProbeDto>
  get_workbench_prompt(promptId: string): Promise<PromptDefinitionDto>
  validate_workbench_prompt(prompt: PromptDefinitionDto): Promise<PromptValidationDto>
  save_workbench_prompt(prompt: PromptDefinitionDto, expectedVersion: number): Promise<PromptValidationDto>
  delete_workbench_prompt(promptId: string): Promise<boolean>
  get_workbench_skill(skillId: string): Promise<SkillDefinitionDto>
  validate_workbench_skill(skill: SkillDefinitionDto): Promise<SkillValidationDto>
  save_workbench_skill(skill: SkillDefinitionDto, expectedVersion: number): Promise<SkillValidationDto>
  delete_workbench_skill(skillId: string): Promise<boolean>
  get_workbench_workflow(targetId: string, workflowId: string): Promise<WorkflowDefinitionDto>
  validate_workbench_workflow(targetId: string, workflow: WorkflowDefinitionDto): Promise<WorkflowValidationDto>
  save_workbench_workflow(targetId: string, workflow: WorkflowDefinitionDto, expectedVersion: number): Promise<WorkflowValidationDto>
  delete_workbench_workflow(targetId: string, workflowId: string): Promise<boolean>
  list_workbench_runs(targetId: string): Promise<WorkflowRunDto[]>
  get_logs(after?: number): Promise<{ cursor: number; entries: LogEntryDto[] }>
  clear_logs(): Promise<number>
  detect_executable(product: string, configured?: string): Promise<{ path: string; source: string; version: string }>
  choose_workspace(initial?: string): Promise<string>
  choose_file(initial?: string): Promise<string>
  check_update(): Promise<ReleaseDto>
  start_update(): Promise<UpdateStatusDto>
  update_status(): Promise<UpdateStatusDto>
  install_update(): Promise<UpdateStatusDto>
  open_external(url: string): Promise<boolean>
}

import type {
  CapabilityCatalogDto,
  DesktopBridge,
  GatewayDiagnosticDto,
  GatewayDraft,
  GatewayDto,
  LogEntryDto,
  MCPConnectionDefinitionDto,
  MCPConnectionProbeDto,
  MCPConnectionValidationDto,
  OAuthClientDto,
  PermissionRequestDto,
  PromptDefinitionDto,
  PromptValidationDto,
  SkillDefinitionDto,
  SkillValidationDto,
  WorkbenchCatalogDto,
  WorkbenchTargetDto,
  WorkflowApprovalDto,
  WorkflowDefinitionDto,
  WorkflowRunDto,
  WorkflowValidationDto,
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
  async promoteServerToGateway(serverId: string, payload: GatewayDraft): Promise<GatewayDto> {
    return (await bridge()).promote_server_to_gateway(serverId, payload)
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
  async testGateway(gatewayId: string): Promise<GatewayDiagnosticDto> {
    return (await bridge()).test_gateway(gatewayId)
  },
  async listOAuthClients(serverId: string): Promise<OAuthClientDto[]> {
    return (await bridge()).list_oauth_clients(serverId)
  },
  async listGatewayOAuthClients(gatewayId: string, serverId: string): Promise<OAuthClientDto[]> {
    return (await bridge()).list_gateway_oauth_clients(gatewayId, serverId)
  },
  async revokeOAuthClient(serverId: string, clientId: string): Promise<boolean> {
    return (await bridge()).revoke_oauth_client(serverId, clientId)
  },
  async revokeAllOAuthClients(serverId: string): Promise<number> {
    return (await bridge()).revoke_all_oauth_clients(serverId)
  },
  async revokeGatewayOAuthClient(gatewayId: string, serverId: string, clientId: string): Promise<boolean> {
    return (await bridge()).revoke_gateway_oauth_client(gatewayId, serverId, clientId)
  },
  async revokeAllGatewayOAuthClients(gatewayId: string, serverId: string): Promise<number> {
    return (await bridge()).revoke_all_gateway_oauth_clients(gatewayId, serverId)
  },
  async listPermissionRequests(): Promise<PermissionRequestDto[]> {
    return (await bridge()).list_permission_requests()
  },
  async respondPermissionRequest(requestId: string, decision: 'deny' | 'once' | 'session'): Promise<boolean> {
    return (await bridge()).respond_permission_request(requestId, decision)
  },
  async listWorkflowApprovals(): Promise<WorkflowApprovalDto[]> {
    return (await bridge()).list_workflow_approvals()
  },
  async respondWorkflowApproval(requestId: string, approved: boolean): Promise<boolean> {
    return (await bridge()).respond_workflow_approval(requestId, approved)
  },
  async listWorkbenchTargets(): Promise<WorkbenchTargetDto[]> {
    return (await bridge()).list_workbench_targets()
  },
  async workbenchCatalog(targetId: string): Promise<WorkbenchCatalogDto> {
    return (await bridge()).get_workbench_catalog(targetId)
  },
  async capabilityCatalog(): Promise<CapabilityCatalogDto> {
    return (await bridge()).get_workbench_capability_catalog()
  },
  async workbenchMCPConnection(connectionId: string): Promise<MCPConnectionDefinitionDto> {
    return (await bridge()).get_workbench_mcp_connection(connectionId)
  },
  async validateWorkbenchMCPConnection(connection: MCPConnectionDefinitionDto): Promise<MCPConnectionValidationDto> {
    return (await bridge()).validate_workbench_mcp_connection(connection)
  },
  async saveWorkbenchMCPConnection(connection: MCPConnectionDefinitionDto, expectedVersion: number): Promise<MCPConnectionValidationDto> {
    return (await bridge()).save_workbench_mcp_connection(connection, expectedVersion)
  },
  async deleteWorkbenchMCPConnection(connectionId: string): Promise<boolean> {
    return (await bridge()).delete_workbench_mcp_connection(connectionId)
  },
  async testWorkbenchMCPConnection(connectionId: string, timeoutSeconds = 8): Promise<MCPConnectionProbeDto> {
    return (await bridge()).test_workbench_mcp_connection(connectionId, timeoutSeconds)
  },
  async discoverWorkbenchMCPConnectionTools(connectionId: string, timeoutSeconds = 8): Promise<MCPConnectionProbeDto> {
    return (await bridge()).discover_workbench_mcp_connection_tools(connectionId, timeoutSeconds)
  },
  async workbenchPrompt(promptId: string): Promise<PromptDefinitionDto> {
    return (await bridge()).get_workbench_prompt(promptId)
  },
  async validateWorkbenchPrompt(prompt: PromptDefinitionDto): Promise<PromptValidationDto> {
    return (await bridge()).validate_workbench_prompt(prompt)
  },
  async saveWorkbenchPrompt(prompt: PromptDefinitionDto, expectedVersion: number): Promise<PromptValidationDto> {
    return (await bridge()).save_workbench_prompt(prompt, expectedVersion)
  },
  async deleteWorkbenchPrompt(promptId: string): Promise<boolean> {
    return (await bridge()).delete_workbench_prompt(promptId)
  },
  async workbenchSkill(skillId: string): Promise<SkillDefinitionDto> {
    return (await bridge()).get_workbench_skill(skillId)
  },
  async validateWorkbenchSkill(skill: SkillDefinitionDto): Promise<SkillValidationDto> {
    return (await bridge()).validate_workbench_skill(skill)
  },
  async saveWorkbenchSkill(skill: SkillDefinitionDto, expectedVersion: number): Promise<SkillValidationDto> {
    return (await bridge()).save_workbench_skill(skill, expectedVersion)
  },
  async deleteWorkbenchSkill(skillId: string): Promise<boolean> {
    return (await bridge()).delete_workbench_skill(skillId)
  },
  async workbenchWorkflow(targetId: string, workflowId: string): Promise<WorkflowDefinitionDto> {
    return (await bridge()).get_workbench_workflow(targetId, workflowId)
  },
  async validateWorkbenchWorkflow(targetId: string, workflow: WorkflowDefinitionDto): Promise<WorkflowValidationDto> {
    return (await bridge()).validate_workbench_workflow(targetId, workflow)
  },
  async saveWorkbenchWorkflow(targetId: string, workflow: WorkflowDefinitionDto, expectedVersion: number): Promise<WorkflowValidationDto> {
    return (await bridge()).save_workbench_workflow(targetId, workflow, expectedVersion)
  },
  async deleteWorkbenchWorkflow(targetId: string, workflowId: string): Promise<boolean> {
    return (await bridge()).delete_workbench_workflow(targetId, workflowId)
  },
  async listWorkbenchRuns(targetId: string): Promise<WorkflowRunDto[]> {
    return (await bridge()).list_workbench_runs(targetId)
  },
  async logs(after = 0): Promise<{ cursor: number; entries: LogEntryDto[] }> {
    return (await bridge()).get_logs(after)
  },
  async clearLogs(): Promise<number> {
    return (await bridge()).clear_logs()
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

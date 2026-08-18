<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { desktopApi } from './api/desktop'
import AboutView from './components/AboutView.vue'
import AppSidebar from './components/AppSidebar.vue'
import LogPanel from './components/LogPanel.vue'
import OAuthClientView from './components/OAuthClientView.vue'
import ServerEditor from './components/ServerEditor.vue'
import ServerList from './components/ServerList.vue'
import { isSelectedServerStarting } from './lib/serverState'
import type { LogEntryDto, OAuthClientDto, PageKey, PermissionRequestDto, ReleaseDto, ServerDraft, ServerDto, UpdateStatusDto } from './types'

const page = ref<PageKey>('servers')
const version = ref('')
const servers = ref<ServerDto[]>([])
const selectedId = ref('')
const draft = ref<ServerDraft>(emptyDraft(8234))
const isNew = ref(true)
const clients = ref<OAuthClientDto[]>([])
const logs = ref<LogEntryDto[]>([])
const logCursor = ref(0)
const busy = ref(false)
const errorMessage = ref('')
const release = ref<ReleaseDto | null>(null)
const checkingUpdate = ref(false)
const updateProxyPrefix = ref('')
const savingUpdateProxy = ref(false)
const startingServerId = ref('')
const permissionRequests = ref<PermissionRequestDto[]>([])
const permissionResponding = ref(false)
const updateStatus = ref<UpdateStatusDto>({
  state: 'idle', version: '', progress: 0, downloaded_bytes: 0, total_bytes: 0, message: '',
})
let installRequested = false
let pollTimer = 0

const selected = computed(() => servers.value.find(item => item.server_id === selectedId.value) || null)
const selectedIsStarting = computed(() => isSelectedServerStarting(selectedId.value, startingServerId.value))
const activePermissionRequest = computed(() => permissionRequests.value[0] || null)
const permissionArguments = computed(() => {
  const request = activePermissionRequest.value
  if (!request) return ''
  try { return JSON.stringify(request.arguments, null, 2) } catch { return String(request.arguments) }
})
const serverStats = computed(() => ({
  total: servers.value.length,
  running: servers.value.filter(item => item.running).length,
  persistent: servers.value.filter(item => item.lifecycle === 'persistent').length,
  oauth: servers.value.reduce((total, item) => total + item.oauth_client_count, 0),
}))

function emptyDraft(port: number): ServerDraft {
  return {
    name: '', workspace: '', oauth_password: '', host: '127.0.0.1', port,
    remember_secrets: true,
    permission_mode: 'safe',
    network: { provider: 'cloudflare', public_url: '', options: {} },
  }
}

function draftFromServer(server: ServerDto): ServerDraft {
  return {
    name: server.name,
    workspace: server.workspace,
    oauth_password: server.oauth_password,
    host: server.host,
    port: server.port,
    remember_secrets: server.has_saved_password || Object.keys(server.network.options).some(key => ['tunnel_token', 'authtoken'].includes(key)),
    permission_mode: server.permission_mode,
    network: {
      provider: server.network.provider,
      public_url: server.network.public_url,
      options: { ...server.network.options },
    },
  }
}

async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  errorMessage.value = ''
  try { await action() } catch (error) { errorMessage.value = error instanceof Error ? error.message : String(error) } finally { busy.value = false }
}

async function refreshServers(preserveDraft = false) {
  servers.value = await desktopApi.listServers()
  if (!preserveDraft && selectedId.value) {
    const server = servers.value.find(item => item.server_id === selectedId.value)
    if (server) draft.value = draftFromServer(server)
  }
}

async function selectServer(serverId: string) {
  selectedId.value = serverId
  isNew.value = false
  await desktopApi.selectServer(serverId)
  const server = servers.value.find(item => item.server_id === serverId)
  if (server) draft.value = draftFromServer(server)
}

async function createNew() {
  selectedId.value = ''
  isNew.value = true
  draft.value = emptyDraft(await desktopApi.nextPort())
}

async function saveServer() {
  await run(async () => {
    const saved = isNew.value
      ? await desktopApi.createServer(draft.value)
      : await desktopApi.updateServer(selectedId.value, draft.value)
    selectedId.value = saved.server_id
    isNew.value = false
    await refreshServers()
  })
}

async function deleteServer() {
  if (!selectedId.value || !confirm('确定删除这个 MCP Server 吗？该 Server 的持久化 OAuth Client 和 token secret 也会被删除。')) return
  await run(async () => {
    await desktopApi.deleteServer(selectedId.value)
    await refreshServers()
    if (servers.value.length) await selectServer(servers.value[0].server_id)
    else await createNew()
  })
}

async function toggleServer(server: ServerDto) {
  await run(async () => {
    const starting = !server.running
    if (starting) startingServerId.value = server.server_id
    try {
      if (server.running) await desktopApi.stopServer(server.server_id)
      else if (server.server_id === selectedId.value) {
        await desktopApi.updateServer(server.server_id, draft.value)
        await desktopApi.startServer(server.server_id, draft.value)
      } else {
        await desktopApi.startServer(server.server_id)
      }
      await refreshServers(true)
    } finally {
      if (startingServerId.value === server.server_id) startingServerId.value = ''
    }
  })
}

async function startSelected() {
  if (!selected.value) return
  await toggleServer(selected.value)
}

async function stopSelected() {
  if (!selected.value) return
  await toggleServer(selected.value)
}

async function refreshClients() {
  clients.value = selectedId.value ? await desktopApi.listOAuthClients(selectedId.value) : []
}

async function refreshPermissionRequests() {
  permissionRequests.value = await desktopApi.listPermissionRequests()
}

function permissionLabel(permission: string) {
  return ({
    network: '访问网络',
    destructive_command: '执行破坏性命令',
    git_metadata_write: '写入 Git 元数据',
    long_timeout: '延长执行时间',
    sensitive_env: '传入敏感环境变量',
    shell_expansion: '使用 Shell 展开',
    inline_script: '执行内联脚本',
    privileged_executable: '查询并运行用户工具',
  } as Record<string, string>)[permission] || permission
}

async function respondPermission(approved: boolean) {
  const request = activePermissionRequest.value
  if (!request || permissionResponding.value) return
  permissionResponding.value = true
  try {
    const accepted = await desktopApi.respondPermissionRequest(request.request_id, approved)
    if (!accepted) errorMessage.value = '授权请求已过期或不再有效。'
    await refreshPermissionRequests()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    permissionResponding.value = false
  }
}

async function openClients(serverId: string) {
  if (serverId) await selectServer(serverId)
  page.value = 'clients'
  await refreshClients()
}

async function changePage(value: PageKey) {
  page.value = value
  if (value === 'clients') await openClients(selectedId.value)
}

async function revokeClient(clientId: string) {
  if (!confirm('撤销后该 AI/MCP 连接需要重新注册并授权。继续吗？')) return
  await run(async () => { await desktopApi.revokeOAuthClient(selectedId.value, clientId); await refreshClients(); await refreshServers(true) })
}

async function revokeAll() {
  if (!confirm('确定撤销当前 Server 的全部 OAuth Client 吗？')) return
  await run(async () => { await desktopApi.revokeAllOAuthClients(selectedId.value); await refreshClients(); await refreshServers(true) })
}

async function checkUpdate() {
  checkingUpdate.value = true
  errorMessage.value = ''
  try { release.value = await desktopApi.checkUpdate() } catch (error) { errorMessage.value = error instanceof Error ? error.message : String(error) } finally { checkingUpdate.value = false }
}

async function saveUpdateProxy(prefix: string) {
  savingUpdateProxy.value = true
  errorMessage.value = ''
  try {
    updateProxyPrefix.value = await desktopApi.saveUpdateDownloadProxy(prefix)
    release.value = null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    savingUpdateProxy.value = false
  }
}

async function startUpdate() {
  errorMessage.value = ''
  installRequested = false
  try {
    updateStatus.value = await desktopApi.startUpdate()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  }
}

async function refreshUpdateStatus() {
  if (!['downloading', 'verifying', 'ready', 'installing'].includes(updateStatus.value.state)) return
  updateStatus.value = await desktopApi.updateStatus()
  if (updateStatus.value.state === 'ready' && !installRequested) {
    installRequested = true
    try {
      updateStatus.value = await desktopApi.installUpdate()
    } catch (error) {
      installRequested = false
      errorMessage.value = error instanceof Error ? error.message : String(error)
      updateStatus.value = await desktopApi.updateStatus()
    }
  }
}

async function poll() {
  try {
    await refreshServers(true)
    const response = await desktopApi.logs(logCursor.value)
    logCursor.value = response.cursor
    logs.value.push(...response.entries)
    if (logs.value.length > 600) logs.value.splice(0, logs.value.length - 600)
    if (page.value === 'clients') await refreshClients()
    await refreshPermissionRequests()
    await refreshUpdateStatus()
  } catch { /* transient polling failures are surfaced by explicit actions */ }
}

onMounted(async () => {
  try {
    const [appVersion, serverItems, persistedSelectedId, proxyPrefix] = await Promise.all([
      desktopApi.appVersion(),
      desktopApi.listServers(),
      desktopApi.selectedServerId(),
      desktopApi.updateDownloadProxy(),
    ])
    version.value = appVersion || ''
    servers.value = serverItems
    updateProxyPrefix.value = proxyPrefix
    if (persistedSelectedId && serverItems.some(item => item.server_id === persistedSelectedId)) {
      await selectServer(persistedSelectedId)
    }
    else if (serverItems.length) await selectServer(serverItems[0].server_id)
    else await createNew()
    await refreshPermissionRequests()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  }
  pollTimer = window.setInterval(poll, 900)
})

onBeforeUnmount(() => window.clearInterval(pollTimer))
</script>

<template>
  <div class="app-shell">
    <AppSidebar :active="page" :version="version" @select="changePage" />
    <main class="main-area">
      <div class="main-content">
        <div v-if="errorMessage" class="error-banner"><span>{{ errorMessage }}</span><button @click="errorMessage = ''">×</button></div>

        <template v-if="page === 'servers'">
          <section class="page-stack">
            <header class="page-header">
              <div>
                <h1>服务</h1>
                <p>管理本机 MCP Server Profile、Workspace、网络入口和 OAuth Registry。</p>
              </div>
            </header>

            <div class="metric-grid">
              <div class="metric-card">
                <span>服务总数</span>
                <strong>{{ serverStats.total }}</strong>
                <small>已保存的 Server Profile</small>
              </div>
              <div class="metric-card">
                <span>正在运行</span>
                <strong>{{ serverStats.running }}</strong>
                <small>{{ serverStats.total - serverStats.running }} 个已停止</small>
              </div>
              <div class="metric-card">
                <span>持久服务</span>
                <strong>{{ serverStats.persistent }}</strong>
                <small>{{ serverStats.total - serverStats.persistent }} 个临时 Session</small>
              </div>
              <div class="metric-card">
                <span>OAuth Clients</span>
                <strong>{{ serverStats.oauth }}</strong>
                <small>动态注册客户端总数</small>
              </div>
            </div>

            <div class="server-workspace">
              <ServerList
                :servers="servers"
                :selected-id="selectedId"
                :starting-id="startingServerId"
                @select="selectServer"
                @toggle="toggleServer"
                @create="createNew"
              />
              <ServerEditor
                v-model="draft"
                :is-new="isNew"
                :running="selected?.running || false"
                :starting="selectedIsStarting"
                :public-mcp-url="selected?.public_mcp_url || ''"
                @save="saveServer"
                @delete="deleteServer"
                @start="startSelected"
                @stop="stopSelected"
              />
            </div>
          </section>
        </template>

        <OAuthClientView v-else-if="page === 'clients'" :servers="servers" :selected-id="selectedId" :clients="clients" @select="openClients" @refresh="refreshClients" @revoke="revokeClient" @revoke-all="revokeAll" />
        <section v-else-if="page === 'logs'" class="content-page page-stack">
          <header class="page-header">
            <div><h1>运行日志</h1><p>查看 MCP Server 与网络提供商的实时运行输出。</p></div>
          </header>
          <LogPanel :logs="logs" />
        </section>
        <AboutView
          v-else
          :version="version"
          :release="release"
          :checking="checkingUpdate"
          :update-status="updateStatus"
          :update-proxy-prefix="updateProxyPrefix"
          :saving-proxy="savingUpdateProxy"
          @check="checkUpdate"
          @update="startUpdate"
          @save-proxy="saveUpdateProxy"
          @open="desktopApi.openExternal"
        />
      </div>
    </main>
  </div>

  <div v-if="activePermissionRequest" class="permission-overlay">
    <section class="permission-dialog" role="dialog" aria-modal="true" aria-labelledby="permission-dialog-title">
      <header class="permission-dialog-header">
        <div>
          <span class="permission-dialog-kicker">需要授权</span>
          <h2 id="permission-dialog-title">{{ permissionLabel(activePermissionRequest.permission) }}</h2>
        </div>
        <span class="permission-server-badge">{{ activePermissionRequest.server_name }}</span>
      </header>
      <p class="permission-dialog-reason">{{ activePermissionRequest.reason }}</p>
      <dl class="permission-dialog-meta">
        <div><dt>工具</dt><dd>{{ activePermissionRequest.tool_name }}</dd></div>
        <div><dt>权限</dt><dd>{{ activePermissionRequest.permission }}</dd></div>
      </dl>
      <div class="permission-arguments">
        <span>本次调用参数（敏感字段已脱敏）</span>
        <pre>{{ permissionArguments }}</pre>
      </div>
      <p class="permission-dialog-note">批准只放行这一次完全相同的调用，其他 Safe 沙箱限制继续生效。</p>
      <footer class="permission-dialog-actions">
        <button class="secondary-button" :disabled="permissionResponding" @click="respondPermission(false)">拒绝</button>
        <button class="primary-button" :disabled="permissionResponding" @click="respondPermission(true)">仅允许本次</button>
      </footer>
    </section>
  </div>
</template>

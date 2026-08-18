<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { desktopApi } from './api/desktop'
import AboutView from './components/AboutView.vue'
import AppSidebar from './components/AppSidebar.vue'
import LogPanel from './components/LogPanel.vue'
import OAuthClientView from './components/OAuthClientView.vue'
import ServerEditor from './components/ServerEditor.vue'
import ServerList from './components/ServerList.vue'
import type { LogEntryDto, OAuthClientDto, PageKey, ReleaseDto, ServerDraft, ServerDto, UpdateStatusDto } from './types'

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
const startingServerId = ref('')
const updateStatus = ref<UpdateStatusDto>({
  state: 'idle', version: '', progress: 0, downloaded_bytes: 0, total_bytes: 0, message: '',
})
let installRequested = false
let pollTimer = 0

const selected = computed(() => servers.value.find(item => item.server_id === selectedId.value) || null)
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
    await refreshUpdateStatus()
  } catch { /* transient polling failures are surfaced by explicit actions */ }
}

onMounted(async () => {
  const data = await desktopApi.bootstrap()
  version.value = data.version
  servers.value = data.servers
  if (data.selected_server_id) await selectServer(data.selected_server_id)
  else await createNew()
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
                :starting="startingServerId === selectedId"
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
          @check="checkUpdate"
          @update="startUpdate"
          @open="desktopApi.openExternal"
        />
      </div>
    </main>
  </div>
</template>

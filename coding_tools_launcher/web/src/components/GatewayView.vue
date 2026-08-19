<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Check, Copy, LoaderCircle, Network, Plus, Trash2 } from '@lucide/vue'
import { desktopApi } from '../api/desktop'
import type {
  GatewayDraft,
  GatewayDto,
  GatewayMemberDraft,
  GatewayMemberDto,
} from '../types'

const gateways = ref<GatewayDto[]>([])
const selectedId = ref('')
const draft = ref<GatewayDraft>(emptyDraft(8234))
const isNew = ref(true)
const busy = ref(false)
const startingId = ref('')
const errorMessage = ref('')
const copiedUrl = ref('')
let pollTimer = 0

const selected = computed(() => gateways.value.find(item => item.gateway_id === selectedId.value) || null)
const locked = computed(() => Boolean(selected.value?.running || startingId.value === selectedId.value))
const stats = computed(() => ({
  total: gateways.value.length,
  running: gateways.value.filter(item => item.running).length,
  members: gateways.value.reduce((count, item) => count + item.members.length, 0),
}))

function emptyMember(index: number): GatewayMemberDraft {
  return {
    name: index === 0 ? 'Company' : `Profile ${index + 1}`,
    workspace: '',
    oauth_password: '',
    instance_path: index === 0 ? '/company' : `/profile-${index + 1}`,
    permission_mode: 'safe',
    allow_network: false,
    enable_view_image: true,
  }
}

function emptyDraft(port: number): GatewayDraft {
  return {
    name: '',
    host: '127.0.0.1',
    port,
    remember_secrets: true,
    network: { provider: 'cloudflare', public_url: '', options: {} },
    members: [emptyMember(0)],
  }
}

function draftFromGateway(gateway: GatewayDto): GatewayDraft {
  return {
    name: gateway.name,
    host: gateway.host,
    port: gateway.port,
    remember_secrets: gateway.members.some(member => member.has_saved_password)
      || Object.keys(gateway.network.options).some(key => ['tunnel_token', 'authtoken'].includes(key)),
    network: {
      provider: gateway.network.provider,
      public_url: gateway.network.public_url,
      options: { ...gateway.network.options },
    },
    members: gateway.members.map(member => ({
      server_id: member.server_id,
      name: member.name,
      workspace: member.workspace,
      oauth_password: member.oauth_password,
      instance_path: member.instance_path,
      permission_mode: member.permission_mode,
      allow_network: member.allow_network,
      enable_view_image: member.enable_view_image,
    })),
  }
}

function memberRuntime(member: GatewayMemberDraft): GatewayMemberDto | null {
  const gateway = selected.value
  if (!gateway || !member.server_id) return null
  return gateway.members.find(item => item.server_id === member.server_id) || null
}

function resolvedMemberUrl(member: GatewayMemberDraft): string {
  const runtime = memberRuntime(member)
  if (runtime?.public_mcp_url) return runtime.public_mcp_url
  const base = draft.value.network.public_url.trim().replace(/\/+$/, '')
  const path = normalizePath(member.instance_path)
  return base && path ? `${base}${path}/mcp` : ''
}

function normalizePath(value: string): string {
  const trimmed = value.trim().replace(/^\/+|\/+$/g, '')
  return trimmed ? `/${trimmed}` : ''
}

function normalizeMemberPath(member: GatewayMemberDraft) {
  member.instance_path = normalizePath(member.instance_path)
}

async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    await action()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
  }
}

async function refresh(preserveDraft = true) {
  gateways.value = await desktopApi.listGateways()
  if (!preserveDraft && selectedId.value) {
    const gateway = gateways.value.find(item => item.gateway_id === selectedId.value)
    if (gateway) draft.value = draftFromGateway(gateway)
  }
}

async function selectGateway(gatewayId: string) {
  selectedId.value = gatewayId
  isNew.value = false
  const gateway = gateways.value.find(item => item.gateway_id === gatewayId)
  if (gateway) draft.value = draftFromGateway(gateway)
}

async function createNew() {
  selectedId.value = ''
  isNew.value = true
  draft.value = emptyDraft(await desktopApi.nextPort())
}

function addMember() {
  if (locked.value) return
  draft.value.members.push(emptyMember(draft.value.members.length))
}

function removeMember(index: number) {
  if (locked.value || draft.value.members.length <= 1) return
  draft.value.members.splice(index, 1)
}

async function chooseWorkspace(member: GatewayMemberDraft) {
  if (locked.value) return
  const value = await desktopApi.chooseWorkspace(member.workspace)
  if (value) member.workspace = value
}

async function saveGateway() {
  await run(async () => {
    draft.value.members.forEach(normalizeMemberPath)
    const saved = isNew.value
      ? await desktopApi.createGateway(draft.value)
      : await desktopApi.updateGateway(selectedId.value, draft.value)
    selectedId.value = saved.gateway_id
    isNew.value = false
    await refresh(false)
  })
}

async function deleteGateway() {
  if (!selectedId.value || !confirm('确定删除这个 Local MCP Gateway 吗？其子 Profile 的 OAuth 状态也会被清理。')) return
  await run(async () => {
    await desktopApi.deleteGateway(selectedId.value)
    await refresh()
    if (gateways.value.length) await selectGateway(gateways.value[0].gateway_id)
    else await createNew()
  })
}

async function toggleGateway() {
  const gateway = selected.value
  if (!gateway) return
  await run(async () => {
    const starting = !gateway.running
    if (starting) startingId.value = gateway.gateway_id
    try {
      if (gateway.running) {
        await desktopApi.stopGateway(gateway.gateway_id)
      } else {
        draft.value.members.forEach(normalizeMemberPath)
        await desktopApi.updateGateway(gateway.gateway_id, draft.value)
        await desktopApi.startGateway(gateway.gateway_id, draft.value)
      }
      await refresh(false)
    } finally {
      if (startingId.value === gateway.gateway_id) startingId.value = ''
    }
  })
}

async function copyUrl(value: string) {
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    copiedUrl.value = value
    window.setTimeout(() => {
      if (copiedUrl.value === value) copiedUrl.value = ''
    }, 1500)
  } catch {
    copiedUrl.value = ''
  }
}

async function poll() {
  try {
    const previousRunning = new Map(gateways.value.map(item => [item.gateway_id, item.running]))
    await refresh(true)
    if (selectedId.value && gateways.value.some(item => item.gateway_id === selectedId.value)) {
      const now = gateways.value.find(item => item.gateway_id === selectedId.value)
      if (now && previousRunning.get(now.gateway_id) !== now.running) {
        draft.value = draftFromGateway(now)
      }
    }
  } catch { /* transient polling failure */ }
}

onMounted(async () => {
  try {
    await refresh()
    if (gateways.value.length) await selectGateway(gateways.value[0].gateway_id)
    else await createNew()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  }
  pollTimer = window.setInterval(poll, 1000)
})

onBeforeUnmount(() => window.clearInterval(pollTimer))
</script>

<template>
  <section class="page-stack gateway-view">
    <header class="page-header">
      <div>
        <h1>Local MCP Gateway</h1>
        <p>一个公网 hostname、一个本地端口，按 Path 承载同一台机器上的多个 MCP Profile。</p>
      </div>
    </header>

    <div v-if="errorMessage" class="error-banner">
      <span>{{ errorMessage }}</span>
      <button @click="errorMessage = ''">×</button>
    </div>

    <div class="metric-grid gateway-metrics">
      <div class="metric-card"><span>Gateway</span><strong>{{ stats.total }}</strong><small>本机入口</small></div>
      <div class="metric-card"><span>正在运行</span><strong>{{ stats.running }}</strong><small>{{ stats.total - stats.running }} 个已停止</small></div>
      <div class="metric-card"><span>Profile</span><strong>{{ stats.members }}</strong><small>共享 Gateway 的 Workspace</small></div>
    </div>

    <div class="gateway-layout">
      <aside class="gateway-list panel-surface">
        <div class="gateway-list-header">
          <div><strong>Gateway</strong><span>单机多 Profile</span></div>
          <button class="secondary-button compact-button" @click="createNew"><Plus :size="14" /> 新建</button>
        </div>
        <button
          v-for="gateway in gateways"
          :key="gateway.gateway_id"
          type="button"
          :class="['gateway-list-item', { active: gateway.gateway_id === selectedId }]"
          @click="selectGateway(gateway.gateway_id)"
        >
          <span :class="['gateway-dot', gateway.running ? 'running' : 'stopped']" />
          <span class="gateway-list-copy">
            <strong>{{ gateway.name }}</strong>
            <small>{{ gateway.members.length }} Profiles · :{{ gateway.port }}</small>
          </span>
        </button>
        <div v-if="!gateways.length" class="empty-state compact-empty">尚未创建 Gateway</div>
      </aside>

      <section class="editor-panel gateway-editor">
        <div class="section-heading">
          <div>
            <h2>{{ isNew ? '新建 Local MCP Gateway' : 'Gateway 设置' }}</h2>
            <p>Cloudflare 等网络层只看到一个 hostname；Path 分流发生在本机 Gateway 内。</p>
          </div>
          <span :class="['status-pill', startingId === selectedId ? 'starting' : selected?.running ? 'running' : 'stopped']">
            <LoaderCircle v-if="startingId === selectedId" class="status-spinner" :size="12" />
            {{ startingId === selectedId ? '启动中…' : selected?.running ? '运行中' : '已停止' }}
          </span>
        </div>

        <div class="form-grid">
          <label class="field span-2">
            <span>Gateway 名称</span>
            <input v-model.trim="draft.name" :disabled="locked" placeholder="例如：本机 MCP Gateway" />
          </label>
          <label class="field">
            <span>本地端口</span>
            <input v-model.number="draft.port" :disabled="locked" type="number" min="1" max="65535" />
          </label>
          <label class="field">
            <span>监听地址</span>
            <input v-model.trim="draft.host" disabled />
          </label>
          <label class="field span-2">
            <span>网络方案</span>
            <select v-model="draft.network.provider" :disabled="locked">
              <option value="cloudflare">Cloudflare Tunnel</option>
              <option value="frp">FRP</option>
              <option value="ngrok">ngrok</option>
              <option value="tailscale">Tailscale Funnel</option>
              <option value="external">自定义公网 URL</option>
            </select>
          </label>
          <label class="field span-2">
            <span>Gateway Public Hostname</span>
            <input
              v-model.trim="draft.network.public_url"
              :disabled="locked || draft.network.provider === 'tailscale'"
              :placeholder="draft.network.provider === 'cloudflare' ? '例如 https://mcp.example.com；留空使用 Quick Tunnel' : '公网 hostname，不要填写 Profile Path'"
            />
            <span class="field-help">这里只配置 hostname。不要填写 /company、/home 等 Path，它们属于下面的 Member。</span>
          </label>

          <label v-if="draft.network.provider === 'cloudflare'" class="field span-2">
            <span>Tunnel Token</span>
            <input v-model="draft.network.options.tunnel_token" :disabled="locked" type="password" placeholder="一个 Gateway 使用一个独立 Named Tunnel Token" />
          </label>
          <template v-else-if="draft.network.provider === 'frp'">
            <label class="field span-2"><span>frpc 路径</span><input v-model.trim="draft.network.options.executable" :disabled="locked" /></label>
            <label class="field span-2"><span>frpc 配置文件</span><input v-model.trim="draft.network.options.config_file" :disabled="locked" /></label>
          </template>
          <template v-else-if="draft.network.provider === 'ngrok'">
            <label class="field"><span>ngrok 路径</span><input v-model.trim="draft.network.options.executable" :disabled="locked" /></label>
            <label class="field"><span>Auth Token</span><input v-model="draft.network.options.authtoken" :disabled="locked" type="password" /></label>
          </template>
          <label v-else-if="draft.network.provider === 'tailscale'" class="field span-2"><span>Tailscale 路径</span><input v-model.trim="draft.network.options.executable" :disabled="locked" /></label>

          <label class="check-field span-2">
            <input v-model="draft.remember_secrets" type="checkbox" />
            <span>在本机持久化 Tunnel Token 与各 Profile OAuth Password</span>
          </label>
        </div>

        <div class="gateway-members-heading">
          <div>
            <strong>Profile Members</strong>
            <span>每个 Member 独立 Workspace、Path、权限与 OAuth Session。</span>
          </div>
          <button class="secondary-button compact-button" :disabled="locked" @click="addMember"><Plus :size="14" /> 添加 Profile</button>
        </div>

        <div class="gateway-members">
          <article v-for="(member, index) in draft.members" :key="member.server_id || `new-${index}`" class="gateway-member-card">
            <header>
              <div class="gateway-member-index"><Network :size="15" /><strong>Profile {{ index + 1 }}</strong></div>
              <button class="icon-danger-button" :disabled="locked || draft.members.length <= 1" title="删除 Profile" @click="removeMember(index)"><Trash2 :size="14" /></button>
            </header>
            <div class="form-grid compact-grid">
              <label class="field"><span>名称</span><input v-model.trim="member.name" :disabled="locked" placeholder="Company" /></label>
              <label class="field"><span>Path</span><input v-model="member.instance_path" :disabled="locked" placeholder="/company" @blur="normalizeMemberPath(member)" /></label>
              <label class="field span-2">
                <span>Workspace</span>
                <div class="input-action">
                  <input v-model.trim="member.workspace" :disabled="locked" placeholder="选择该 Profile 的项目目录" />
                  <button type="button" :disabled="locked" @click="chooseWorkspace(member)">选择</button>
                </div>
              </label>
              <label class="field"><span>OAuth Password</span><input v-model="member.oauth_password" type="password" placeholder="授权页密码" /></label>
              <label class="field">
                <span>权限模式</span>
                <select v-model="member.permission_mode" :disabled="locked">
                  <option value="safe">Safe</option>
                  <option value="trusted">Trusted</option>
                  <option value="dangerous">Dangerous</option>
                </select>
              </label>
              <label class="check-field"><input v-model="member.allow_network" :disabled="locked" type="checkbox" /><span>允许网络</span></label>
              <label class="check-field"><input v-model="member.enable_view_image" :disabled="locked" type="checkbox" /><span>启用图片工具</span></label>
            </div>
            <div v-if="resolvedMemberUrl(member)" class="gateway-url-row">
              <code>{{ resolvedMemberUrl(member) }}</code>
              <button class="copy-mini" @click="copyUrl(resolvedMemberUrl(member))">
                <Check v-if="copiedUrl === resolvedMemberUrl(member)" :size="13" />
                <Copy v-else :size="13" />
              </button>
            </div>
          </article>
        </div>

        <div class="editor-actions">
          <button v-if="!isNew" class="danger-button" :disabled="busy || locked" @click="deleteGateway">删除</button>
          <div class="editor-actions-right">
            <button class="secondary-button" :disabled="busy || locked" @click="saveGateway">{{ isNew ? '创建 Gateway' : '保存' }}</button>
            <button v-if="!isNew && !selected?.running" class="primary-button" :disabled="busy || startingId === selectedId" @click="toggleGateway">启动 Gateway</button>
            <button v-else-if="!isNew" class="danger-button" :disabled="busy" @click="toggleGateway">停止 Gateway</button>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.gateway-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.gateway-layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 16px; align-items: start; }
.gateway-list { overflow: hidden; border: 1px solid var(--border); border-radius: 10px; background: var(--card); }
.gateway-list-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px; border-bottom: 1px solid var(--border); }
.gateway-list-header > div { display: grid; gap: 2px; }
.gateway-list-header span { color: var(--muted-foreground); font-size: 11px; }
.gateway-list-item { width: 100%; min-height: 58px; display: flex; align-items: center; gap: 10px; padding: 10px 14px; border: 0; border-bottom: 1px solid var(--border); background: transparent; color: inherit; text-align: left; cursor: pointer; }
.gateway-list-item:hover, .gateway-list-item.active { background: var(--secondary); }
.gateway-dot { width: 8px; height: 8px; flex: 0 0 auto; border-radius: 999px; background: #F56C6C; }
.gateway-dot.running { background: #67C23A; }
.gateway-list-copy { min-width: 0; display: grid; gap: 3px; }
.gateway-list-copy strong, .gateway-list-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gateway-list-copy small { color: var(--muted-foreground); font-size: 11px; }
.compact-empty { padding: 24px 14px; }
.compact-button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 30px; padding: 0 10px; }
.gateway-members-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 22px; margin-bottom: 10px; }
.gateway-members-heading > div { display: grid; gap: 3px; }
.gateway-members-heading span { color: var(--muted-foreground); font-size: 12px; }
.gateway-members { display: grid; gap: 12px; }
.gateway-member-card { border: 1px solid var(--border); border-radius: 9px; background: color-mix(in srgb, var(--card) 82%, transparent); overflow: hidden; }
.gateway-member-card > header { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.gateway-member-index { display: flex; align-items: center; gap: 7px; }
.compact-grid { padding: 12px; }
.icon-danger-button, .copy-mini { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border: 1px solid var(--border); border-radius: 6px; background: transparent; color: var(--muted-foreground); cursor: pointer; }
.icon-danger-button:hover:not(:disabled) { color: #F56C6C; background: color-mix(in srgb, #F56C6C 10%, transparent); }
.icon-danger-button:disabled { opacity: .35; cursor: not-allowed; }
.gateway-url-row { display: flex; align-items: center; gap: 8px; padding: 9px 12px; border-top: 1px solid var(--border); background: color-mix(in srgb, var(--secondary) 45%, transparent); }
.gateway-url-row code { min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; color: var(--muted-foreground); }
.copy-mini { flex: 0 0 auto; }
.editor-actions { justify-content: space-between; }
.editor-actions-right { margin-left: auto; display: flex; align-items: center; gap: 8px; }
@media (max-width: 980px) { .gateway-layout { grid-template-columns: 1fr; } .gateway-metrics { grid-template-columns: 1fr; } }
</style>

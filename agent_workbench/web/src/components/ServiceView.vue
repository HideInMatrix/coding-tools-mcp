<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Check, Copy, Eye, EyeOff, LoaderCircle, Plus, Trash2 } from '@lucide/vue'
import {
  InputGroup,
  InputGroupButton,
  InputGroupInput,
} from '@/components/ui/input-group'
import { desktopApi } from '../api/desktop'
import { isSelectedResourceStarting } from '../lib/serverState'
import type {
  GatewayDiagnosticDto,
  GatewayDraft,
  GatewayDto,
  GatewayMemberDraft,
  NetworkConfigDto,
  ServerDraft,
  ServerDto,
} from '../types'

type ServiceItem =
  | { key: string; kind: 'direct'; id: string; server: ServerDto }
  | { key: string; kind: 'gateway'; id: string; gateway: GatewayDto }

const servers = ref<ServerDto[]>([])
const gateways = ref<GatewayDto[]>([])
const selectedKey = ref('')
const draft = ref<GatewayDraft>(emptyDraft(8234))
const isNew = ref(true)
const busy = ref(false)
const startingKey = ref('')
const errorMessage = ref('')
const copiedUrl = ref('')
const diagnostic = ref<GatewayDiagnosticDto | null>(null)
const tunnelTokenVisible = ref(false)
const oauthPasswordVisibility = ref(new Map<GatewayMemberDraft, boolean>())
let pollTimer = 0

const services = computed<ServiceItem[]>(() => [
  ...servers.value.map(server => ({
    key: `direct:${server.server_id}`,
    kind: 'direct' as const,
    id: server.server_id,
    server,
  })),
  ...gateways.value.map(gateway => ({
    key: `gateway:${gateway.gateway_id}`,
    kind: 'gateway' as const,
    id: gateway.gateway_id,
    gateway,
  })),
])

const selected = computed(() => services.value.find(item => item.key === selectedKey.value) || null)
const selectedRunning = computed(() => selected.value?.kind === 'direct'
  ? selected.value.server.running
  : selected.value?.kind === 'gateway'
    ? selected.value.gateway.running
    : false)
const selectedIsStarting = computed(() => isSelectedResourceStarting(selectedKey.value, startingKey.value))
const locked = computed(() => Boolean(selectedRunning.value || selectedIsStarting.value))
const stats = computed(() => ({
  services: services.value.length,
  running: services.value.filter(item => item.kind === 'direct' ? item.server.running : item.gateway.running).length,
  workspaces: servers.value.length + gateways.value.reduce((sum, gateway) => sum + gateway.members.length, 0),
}))

function emptyMember(index: number): GatewayMemberDraft {
  return {
    name: index === 0 ? '主 Workspace' : `Profile ${index + 1}`,
    workspace: '',
    oauth_password: '',
    instance_path: index === 0 ? '' : `/profile-${index + 1}`,
    permission_mode: 'safe',
    allow_network: false,
    enable_view_image: true,
  }
}

function emptyDraft(port: number): GatewayDraft {
  return {
    name: '',
    mode: 'single',
    host: '127.0.0.1',
    port,
    remember_secrets: true,
    network: { provider: 'cloudflare', public_url: '', options: {} },
    members: [emptyMember(0)],
  }
}

function directDraft(server: ServerDto): GatewayDraft {
  return {
    name: server.name,
    mode: 'single',
    host: server.host,
    port: server.port,
    remember_secrets: server.has_saved_password
      || Object.keys(server.network.options).some(key => ['tunnel_token', 'authtoken'].includes(key)),
    network: {
      provider: server.network.provider,
      public_url: server.network.public_url,
      options: { ...server.network.options },
    },
    members: [{
      server_id: server.server_id,
      name: '主 Workspace',
      workspace: server.workspace,
      oauth_password: server.oauth_password,
      instance_path: '',
      permission_mode: server.permission_mode,
      allow_network: server.allow_network,
      enable_view_image: server.enable_view_image,
    }],
  }
}

function gatewayDraft(gateway: GatewayDto): GatewayDraft {
  return {
    name: gateway.name,
    mode: gateway.mode,
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

function normalizePath(value: string): string {
  const trimmed = value.trim().replace(/^\/+|\/+$/g, '')
  return trimmed ? `/${trimmed}` : ''
}

function normalizedDraft(): GatewayDraft {
  return {
    ...draft.value,
    network: {
      ...draft.value.network,
      options: { ...draft.value.network.options },
    },
    members: draft.value.members.map(member => ({
      ...member,
      instance_path: normalizePath(member.instance_path),
    })),
  }
}

function serverDraft(value: GatewayDraft): ServerDraft {
  const root = value.members[0]
  return {
    name: value.name,
    workspace: root.workspace,
    oauth_password: root.oauth_password,
    host: value.host,
    port: value.port,
    remember_secrets: value.remember_secrets,
    permission_mode: root.permission_mode,
    allow_network: root.allow_network,
    enable_view_image: root.enable_view_image,
    network: cloneNetwork(value.network),
  }
}

function cloneNetwork(network: NetworkConfigDto): NetworkConfigDto {
  return { ...network, options: { ...network.options } }
}

function runtimeUrl(member: GatewayMemberDraft): string {
  const current = selected.value
  if (current?.kind === 'direct' && member.server_id === current.server.server_id) {
    if (current.server.public_mcp_url) return current.server.public_mcp_url
  }
  if (current?.kind === 'gateway' && member.server_id) {
    const runtime = current.gateway.members.find(item => item.server_id === member.server_id)
    if (runtime?.public_mcp_url) return runtime.public_mcp_url
  }
  const base = draft.value.network.public_url.trim().replace(/\/+$/, '')
  const path = normalizePath(member.instance_path)
  return base ? `${base}${path}/mcp` : ''
}

function serviceName(item: ServiceItem): string {
  return item.kind === 'direct' ? item.server.name : item.gateway.name
}

function servicePort(item: ServiceItem): number {
  return item.kind === 'direct' ? item.server.port : item.gateway.port
}

function serviceRunning(item: ServiceItem): boolean {
  return item.kind === 'direct' ? item.server.running : item.gateway.running
}

function serviceProfileCount(item: ServiceItem): number {
  return item.kind === 'direct' ? 1 : item.gateway.members.length
}

function serviceMode(item: ServiceItem): 'single' | 'multi' {
  return item.kind === 'direct' ? 'single' : item.gateway.mode
}

function isRootProfile(member: GatewayMemberDraft, index: number): boolean {
  if (isNew.value) return index === 0
  const current = selected.value
  if (current?.kind === 'direct') return index === 0
  if (current?.kind === 'gateway' && member.server_id) {
    return Boolean(
      current.gateway.members.find(
        item => item.server_id === member.server_id && item.instance_path === '',
      ),
    )
  }
  return false
}

function profileEnabled(member: GatewayMemberDraft, index: number): boolean {
  return isRootProfile(member, index) || draft.value.mode === 'multi'
}

function setMode(mode: 'single' | 'multi') {
  if (locked.value || draft.value.mode === mode) return
  if (mode === 'single') {
    const hasRoot = draft.value.members.some((member, index) => isRootProfile(member, index))
    if (!hasRoot) {
      errorMessage.value = '当前服务没有根 Workspace。请先将一个 Profile 的 Path 清空为根路径，再切换到单 Workspace。'
      return
    }
  }
  draft.value.mode = mode
  if (mode === 'multi' && draft.value.members.length === 1) {
    draft.value.members.push(emptyMember(1))
  }
}

async function refresh(preserveSelection = true) {
  const previous = selectedKey.value
  const [serverItems, gatewayItems] = await Promise.all([
    desktopApi.listServers(),
    desktopApi.listGateways(),
  ])
  servers.value = serverItems
  gateways.value = gatewayItems
  if (preserveSelection && previous && services.value.some(item => item.key === previous)) return
  if (!isNew.value) selectedKey.value = services.value[0]?.key || ''
}

async function selectService(key: string) {
  const service = services.value.find(item => item.key === key)
  if (!service) return
  tunnelTokenVisible.value = false
  oauthPasswordVisibility.value.clear()
  selectedKey.value = key
  isNew.value = false
  diagnostic.value = service.kind === 'gateway' ? service.gateway.diagnostic : null
  draft.value = service.kind === 'direct' ? directDraft(service.server) : gatewayDraft(service.gateway)
}

async function createNew() {
  tunnelTokenVisible.value = false
  oauthPasswordVisibility.value.clear()
  selectedKey.value = ''
  isNew.value = true
  diagnostic.value = null
  draft.value = emptyDraft(await desktopApi.nextPort())
}

function isOAuthPasswordVisible(member: GatewayMemberDraft): boolean {
  return oauthPasswordVisibility.value.get(member) === true
}

function toggleOAuthPassword(member: GatewayMemberDraft) {
  oauthPasswordVisibility.value.set(member, !isOAuthPasswordVisible(member))
}

function addProfile() {
  if (locked.value) return
  const member = emptyMember(draft.value.members.length)
  draft.value.members.push(member)
}

function removeProfile(index: number) {
  if (locked.value || draft.value.members.length <= 1) return
  const member = draft.value.members[index]
  // New/Direct services keep the first root Workspace stable at /mcp.
  if (isRootProfile(member, index)) return
  if (draft.value.mode === 'multi' && draft.value.members.length <= 2) {
    errorMessage.value = '多 Workspace 模式至少保留一个子 Profile。请先切换到单 Workspace，再删除最后一个子 Profile。'
    return
  }
  draft.value.members.splice(index, 1)
}

async function chooseWorkspace(member: GatewayMemberDraft) {
  if (locked.value) return
  const value = await desktopApi.chooseWorkspace(member.workspace)
  if (value) member.workspace = value
}

async function persistDraft(): Promise<string> {
  const value = normalizedDraft()
  if (!value.members.length) throw new Error('服务至少需要一个 Workspace。')
  const current = selected.value

  if (isNew.value) {
    if (value.mode === 'single' && value.members.length === 1 && value.members[0].instance_path === '') {
      const created = await desktopApi.createServer(serverDraft(value))
      return `direct:${created.server_id}`
    }
    const created = await desktopApi.createGateway(value)
    return `gateway:${created.gateway_id}`
  }

  if (!current) throw new Error('找不到当前服务。')
  if (current.kind === 'direct') {
    if (value.mode === 'single' && value.members.length === 1 && value.members[0].instance_path === '') {
      const updated = await desktopApi.updateServer(current.id, serverDraft(value))
      return `direct:${updated.server_id}`
    }
    const promoted = await desktopApi.promoteServerToGateway(current.id, value)
    return `gateway:${promoted.gateway_id}`
  }

  const updated = await desktopApi.updateGateway(current.id, value)
  return `gateway:${updated.gateway_id}`
}

async function saveService() {
  if (busy.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const key = await persistDraft()
    isNew.value = false
    await refresh(false)
    await selectService(key)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
  }
}

async function deleteService() {
  const current = selected.value
  if (!current || !confirm('确定删除这个服务吗？相关 OAuth 状态也会按现有清理规则处理。')) return
  busy.value = true
  errorMessage.value = ''
  try {
    if (current.kind === 'direct') await desktopApi.deleteServer(current.id)
    else await desktopApi.deleteGateway(current.id)
    await refresh(false)
    if (services.value.length) await selectService(services.value[0].key)
    else await createNew()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
  }
}

async function toggleService() {
  const current = selected.value
  if (!current || busy.value) return
  busy.value = true
  errorMessage.value = ''
  const starting = !serviceRunning(current)
  if (starting) startingKey.value = current.key
  try {
    if (!starting) {
      if (current.kind === 'direct') await desktopApi.stopServer(current.id)
      else await desktopApi.stopGateway(current.id)
    } else {
      const key = await persistDraft()
      startingKey.value = key
      await refresh(false)
      await selectService(key)
      const saved = selected.value
      if (!saved) throw new Error('保存后的服务状态不可用。')
      const value = normalizedDraft()
      if (saved.kind === 'direct') await desktopApi.startServer(saved.id, serverDraft(value))
      else await desktopApi.startGateway(saved.id, value)
    }
    await refresh(false)
    if (selectedKey.value) await selectService(selectedKey.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    startingKey.value = ''
    busy.value = false
  }
}

async function testService() {
  const current = selected.value
  if (current?.kind !== 'gateway' || current.gateway.mode !== 'multi' || !current.gateway.running || busy.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    diagnostic.value = await desktopApi.testGateway(current.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
  }
}

async function copyUrl(value: string) {
  if (!value) return
  await navigator.clipboard.writeText(value)
  copiedUrl.value = value
  window.setTimeout(() => {
    if (copiedUrl.value === value) copiedUrl.value = ''
  }, 1500)
}

async function poll() {
  if (busy.value) return
  try {
    const currentKey = selectedKey.value
    await refresh(true)
    if (currentKey && services.value.some(item => item.key === currentKey)) {
      const current = services.value.find(item => item.key === currentKey)
      if (current?.kind === 'gateway' && current.gateway.diagnostic) {
        diagnostic.value = current.gateway.diagnostic
      }
    }
  } catch { /* transient polling failure */ }
}

onMounted(async () => {
  try {
    await refresh(false)
    if (services.value.length) await selectService(services.value[0].key)
    else await createNew()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  }
  pollTimer = window.setInterval(poll, 1000)
})

onBeforeUnmount(() => window.clearInterval(pollTimer))
</script>

<template>
  <section class="grid gap-5">
    <header class="flex min-h-8 items-center justify-between gap-4">
      <div>
        <h1 class="m-0 text-xl leading-7 font-medium tracking-[-0.02em]">服务</h1>
        <p class="mt-[3px] mb-0 text-xs leading-[18px] text-muted-foreground">一个服务管理一个公网入口；Profile 配置与运行模式分离，由顶部滑块明确决定本次启用单 Workspace 还是多 Workspace。</p>
      </div>
    </header>

    <div v-if="errorMessage" class="sticky top-2 z-30 mb-4 flex items-center justify-between gap-3 rounded-[7px] border border-destructive/25 bg-destructive/10 px-3 py-2.5 text-xs text-destructive">
      <span>{{ errorMessage }}</span><button class="border-0 bg-transparent text-lg leading-none text-inherit" @click="errorMessage = ''">×</button>
    </div>

    <div class="grid grid-cols-1 gap-2 lg:grid-cols-3">
      <div class="min-h-28 rounded-lg bg-card p-4"><span class="block text-xs leading-5 text-muted-foreground">服务</span><strong class="mt-2.5 block min-h-8 text-2xl leading-8 font-medium tracking-[-0.03em] tabular-nums">{{ stats.services }}</strong><small class="mt-1 block text-[11px] leading-4 text-muted-foreground">统一公网入口</small></div>
      <div class="min-h-28 rounded-lg bg-card p-4"><span class="block text-xs leading-5 text-muted-foreground">正在运行</span><strong class="mt-2.5 block min-h-8 text-2xl leading-8 font-medium tracking-[-0.03em] tabular-nums">{{ stats.running }}</strong><small class="mt-1 block text-[11px] leading-4 text-muted-foreground">{{ stats.services - stats.running }} 个已停止</small></div>
      <div class="min-h-28 rounded-lg bg-card p-4"><span class="block text-xs leading-5 text-muted-foreground">Workspace</span><strong class="mt-2.5 block min-h-8 text-2xl leading-8 font-medium tracking-[-0.03em] tabular-nums">{{ stats.workspaces }}</strong><small class="mt-1 block text-[11px] leading-4 text-muted-foreground">主 Workspace + 子 Profile</small></div>
    </div>

    <div class="grid items-start gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
      <aside class="overflow-hidden rounded-[10px] border border-border bg-card">
        <div class="flex items-center justify-between gap-3 border-b border-border p-3.5">
          <div class="grid gap-[3px]"><strong>服务</strong><span class="text-[11px] text-muted-foreground">单入口，多 Workspace 可选</span></div>
          <button class="secondary-button min-h-[30px] px-2.5" @click="createNew"><Plus :size="14" /> 新建</button>
        </div>
        <button
          v-for="service in services"
          :key="service.key"
          type="button"
          :class="[
            'flex min-h-[58px] w-full cursor-pointer items-center justify-start gap-2.5 border-0 border-b border-border bg-transparent px-3.5 py-2.5 text-left text-inherit hover:bg-secondary',
            { 'bg-secondary': service.key === selectedKey },
          ]"
          @click="selectService(service.key)"
        >
          <span :class="['h-2 w-2 shrink-0 rounded-full', serviceRunning(service) ? 'bg-[#67C23A]' : 'bg-[#F56C6C]']" />
          <span class="grid min-w-0 flex-1 justify-items-start gap-[3px] text-left">
            <strong class="w-full truncate text-left">{{ serviceName(service) }}</strong>
            <small class="w-full truncate text-left text-[11px] text-muted-foreground">{{ serviceMode(service) === 'single' ? '单 Workspace' : '多 Workspace' }} · {{ serviceProfileCount(service) }} 个配置 · :{{ servicePort(service) }}</small>
          </span>
        </button>
        <div v-if="!services.length" class="flex min-h-[250px] flex-col items-center justify-center px-5 py-[42px] text-center text-muted-foreground">尚未创建服务</div>
      </aside>

      <section class="overflow-hidden rounded-lg border border-border bg-popover p-4 shadow-sm">
        <div class="mb-4 flex items-center justify-between gap-3.5">
          <div>
            <h2 class="m-0 text-[13px] leading-5 font-medium">{{ isNew ? '新建服务' : '服务设置' }}</h2>
            <p class="mt-px mb-0 text-[11px] leading-4 text-muted-foreground">运行模式由你明确选择；子 Profile 配置可以保留，但单 Workspace 模式不会启动它们。</p>
          </div>
          <span
            :class="[
              'inline-flex min-h-[22px] items-center whitespace-nowrap rounded-full px-2 text-[10px] font-medium',
              selectedIsStarting
                ? 'gap-[5px] bg-stone-500/10 text-stone-600'
                : selectedRunning
                  ? 'bg-success/10 text-success'
                  : 'bg-secondary text-muted-foreground',
            ]"
          >
            <LoaderCircle v-if="selectedIsStarting" class="animate-spin" :size="12" />
            {{ selectedIsStarting ? '启动中…' : selectedRunning ? '运行中' : '已停止' }}
          </span>
        </div>

        <div class="mt-3.5 inline-flex w-fit items-center gap-0.5 rounded-lg border border-border bg-secondary p-[3px]" role="tablist" aria-label="Workspace 运行模式">
          <button
            type="button"
            role="tab"
            :class="[
              'min-h-[30px] cursor-pointer rounded-md border-0 bg-transparent px-3.5 text-[11px] font-medium text-muted-foreground disabled:cursor-not-allowed disabled:opacity-60',
              { 'bg-yellow-400 text-black shadow-sm': draft.mode === 'single' },
            ]"
            :aria-selected="draft.mode === 'single'"
            :disabled="locked"
            @click="setMode('single')"
          >
            单 Workspace
          </button>
          <button
            type="button"
            role="tab"
            :class="[
              'min-h-[30px] cursor-pointer rounded-md border-0 bg-transparent px-3.5 text-[11px] font-medium text-muted-foreground disabled:cursor-not-allowed disabled:opacity-60',
              { 'bg-yellow-400 text-black shadow-sm': draft.mode === 'multi' },
            ]"
            :aria-selected="draft.mode === 'multi'"
            :disabled="locked"
            @click="setMode('multi')"
          >
            多 Workspace
          </button>
        </div>
        <p class="mt-[7px] mb-0 text-[11px] leading-[18px] text-muted-foreground">
          {{ draft.mode === 'single'
            ? '当前只启动主 Workspace；已配置的子 Profile 会保留，但不会参与本次运行。'
            : '当前启动主 Workspace 与全部子 Profile，并按 Path 在同一端口分流。' }}
        </p>

        <div class="form-grid">
          <label class="field span-2"><span>服务名称</span><input v-model.trim="draft.name" :disabled="locked" placeholder="例如：公司开发环境" /></label>
          <label class="field"><span>本地端口</span><input v-model.number="draft.port" :disabled="locked" type="number" min="1" max="65535" /></label>
          <label class="field"><span>监听地址</span><input v-model.trim="draft.host" disabled /></label>
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
            <span>Public Hostname</span>
            <input v-model.trim="draft.network.public_url" :disabled="locked || draft.network.provider === 'tailscale'" placeholder="例如 https://mcp.example.com；Quick Tunnel 可留空" />
          </label>
          <label v-if="draft.network.provider === 'cloudflare'" class="field span-2">
            <span>Tunnel Token</span>
            <InputGroup>
              <InputGroupInput
                v-model="draft.network.options.tunnel_token"
                :disabled="locked"
                :type="tunnelTokenVisible ? 'text' : 'password'"
                autocomplete="off"
              />
              <InputGroupButton
                :aria-label="tunnelTokenVisible ? '隐藏 Tunnel Token' : '显示 Tunnel Token'"
                :aria-pressed="tunnelTokenVisible"
                :title="tunnelTokenVisible ? '隐藏 Tunnel Token' : '显示 Tunnel Token'"
                @click="tunnelTokenVisible = !tunnelTokenVisible"
              >
                <EyeOff v-if="tunnelTokenVisible" :size="15" />
                <Eye v-else :size="15" />
              </InputGroupButton>
            </InputGroup>
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
          <label class="check-field span-2"><input v-model="draft.remember_secrets" type="checkbox" /><span>在本机持久化网络 Token 与 OAuth Password</span></label>
        </div>

        <div class="mt-[22px] mb-2.5 flex items-center justify-between gap-4">
          <div class="grid gap-[3px]"><strong>Workspace Profiles</strong><span class="text-[11px] text-muted-foreground">Profile 是配置；是否启用由上面的运行模式决定。</span></div>
          <button class="secondary-button min-h-[30px] px-2.5" :disabled="locked" @click="addProfile"><Plus :size="14" /> 添加 Profile</button>
        </div>

        <div class="grid gap-3">
          <article
            v-for="(member, index) in draft.members"
            :key="member.server_id || `new-${index}`"
            :class="['overflow-hidden rounded-[9px] border border-border bg-card', { 'opacity-60': !profileEnabled(member, index) }]"
          >
            <header class="flex items-center justify-between border-b border-border px-3 py-2.5">
              <div class="flex items-center gap-2">
                <strong>{{ isRootProfile(member, index) ? '主 Workspace' : `Profile ${index + 1}` }}</strong>
                <code class="text-[11px] text-muted-foreground">{{ isRootProfile(member, index) ? '/mcp' : (normalizePath(member.instance_path) || '需要 Path') }}</code>
                <span v-if="!profileEnabled(member, index)" class="rounded-full border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">未启用</span>
              </div>
              <button class="inline-flex h-7 w-7 items-center justify-center rounded-md border border-border bg-transparent text-muted-foreground" :disabled="locked || draft.members.length <= 1 || isRootProfile(member, index)" @click="removeProfile(index)"><Trash2 :size="14" /></button>
            </header>
            <div class="form-grid p-3">
              <label class="field"><span>名称</span><input v-model.trim="member.name" :disabled="locked" /></label>
              <label class="field">
                <span>Path</span>
                <input v-if="!isRootProfile(member, index)" v-model="member.instance_path" :disabled="locked" placeholder="/api" />
                <input v-else value="根路径 /mcp" disabled />
              </label>
              <label class="field span-2">
                <span>Workspace</span>
                <div class="input-action"><input v-model.trim="member.workspace" :disabled="locked" /><button type="button" :disabled="locked" @click="chooseWorkspace(member)">选择</button></div>
              </label>
              <label class="field">
                <span>OAuth Password</span>
                <InputGroup>
                  <InputGroupInput
                    v-model="member.oauth_password"
                    :disabled="locked"
                    :type="isOAuthPasswordVisible(member) ? 'text' : 'password'"
                    autocomplete="off"
                  />
                  <InputGroupButton
                    :aria-label="isOAuthPasswordVisible(member) ? '隐藏 OAuth Password' : '显示 OAuth Password'"
                    :aria-pressed="isOAuthPasswordVisible(member)"
                    :title="isOAuthPasswordVisible(member) ? '隐藏 OAuth Password' : '显示 OAuth Password'"
                    @click="toggleOAuthPassword(member)"
                  >
                    <EyeOff v-if="isOAuthPasswordVisible(member)" :size="15" />
                    <Eye v-else :size="15" />
                  </InputGroupButton>
                </InputGroup>
              </label>
              <label class="field"><span>权限模式</span><select v-model="member.permission_mode" :disabled="locked"><option value="safe">Safe</option><option value="trusted">Trusted</option><option value="dangerous">Dangerous</option></select></label>
              <label class="check-field"><input v-model="member.allow_network" :disabled="locked" type="checkbox" /><span>允许网络</span></label>
              <label class="check-field"><input v-model="member.enable_view_image" :disabled="locked" type="checkbox" /><span>启用图片工具</span></label>
            </div>
            <div v-if="runtimeUrl(member)" class="flex items-center gap-2 border-t border-border bg-secondary/50 px-3 py-[9px]">
              <code class="min-w-0 flex-1 truncate text-[11px] font-medium text-blue-600 dark:text-blue-400">{{ runtimeUrl(member) }}</code>
              <button class="inline-flex h-7 w-7 items-center justify-center rounded-md border border-border bg-transparent text-muted-foreground" @click="copyUrl(runtimeUrl(member))"><Check v-if="copiedUrl === runtimeUrl(member)" :size="13" /><Copy v-else :size="13" /></button>
            </div>
          </article>
        </div>

        <section v-if="selected?.kind === 'gateway' && selected.gateway.mode === 'multi' && selected.gateway.running" class="mt-4 flex items-center gap-3 rounded-lg border border-border px-3 py-[11px]">
          <div class="grid min-w-0 flex-1 gap-[3px]"><strong>公网 E2E 自检</strong><span class="text-[11px] text-muted-foreground">多 Workspace 服务会验证每个 Path 对应的 Runtime 与 OAuth metadata。</span></div>
          <button class="secondary-button" :disabled="busy" @click="testService">开始自检</button>
          <span v-if="diagnostic" :class="diagnostic.ok ? 'text-[#67C23A]' : 'text-[#F56C6C]'">{{ diagnostic.ok ? '全部通过' : '存在失败项' }}</span>
        </section>

        <div class="mt-4 flex justify-between gap-2 border-t border-border pt-3.5">
          <button v-if="!isNew" class="danger-button" :disabled="busy || locked" @click="deleteService">删除</button>
          <div class="ml-auto flex gap-2">
            <button class="secondary-button" :disabled="busy || locked" @click="saveService">{{ isNew ? '创建服务' : '保存' }}</button>
            <button v-if="!isNew && !selectedRunning" class="primary-button" :disabled="busy || selectedIsStarting" @click="toggleService">启动</button>
            <button v-else-if="!isNew" class="danger-button" :disabled="busy" @click="toggleService">停止</button>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Button } from '@/components/ui/button'
import { desktopApi } from '../api/desktop'
import type { GatewayDto, OAuthClientDto, ServerDto } from '../types'

type OAuthTarget =
  | {
      key: string
      kind: 'server'
      serverId: string
      label: string
      detail: string
      running: boolean
      lifecycle: 'persistent' | 'ephemeral'
      clientCount: number
    }
  | {
      key: string
      kind: 'gateway'
      gatewayId: string
      serverId: string
      label: string
      detail: string
      running: boolean
      lifecycle: 'persistent' | 'ephemeral'
      clientCount: number
    }

const servers = ref<ServerDto[]>([])
const gateways = ref<GatewayDto[]>([])
const selectedKey = ref('')
const clients = ref<OAuthClientDto[]>([])
const busy = ref(false)
const errorMessage = ref('')
let pollTimer = 0

const targets = computed<OAuthTarget[]>(() => [
  ...servers.value.map(server => ({
    key: `server:${server.server_id}`,
    kind: 'server' as const,
    serverId: server.server_id,
    label: `服务 · ${server.name} / 主 Workspace`,
    detail: `/mcp · ${server.host}:${server.port}`,
    running: server.running,
    lifecycle: server.lifecycle,
    clientCount: server.oauth_client_count,
  })),
  ...gateways.value.flatMap(gateway => gateway.members.map(member => ({
    key: `gateway:${gateway.gateway_id}:${member.server_id}`,
    kind: 'gateway' as const,
    gatewayId: gateway.gateway_id,
    serverId: member.server_id,
    label: `服务 · ${gateway.name} / ${member.instance_path ? member.name : '主 Workspace'}`,
    detail: member.instance_path ? `${member.instance_path}/mcp` : '/mcp',
    running: gateway.running && (gateway.mode === 'multi' || member.instance_path === ''),
    lifecycle: member.lifecycle,
    clientCount: member.oauth_client_count,
  }))),
])

const selected = computed(() => targets.value.find(item => item.key === selectedKey.value) || null)
const canMutate = computed(() => Boolean(
  selected.value
    && !selected.value.running
    && selected.value.lifecycle === 'persistent',
))
const mutationHelp = computed(() => {
  const target = selected.value
  if (!target) return '请选择一个 OAuth 授权目标。'
  if (target.lifecycle === 'ephemeral') {
    return target.running
      ? '当前目标使用临时公网 Session；OAuth Client 会在服务停止时自动销毁。'
      : '该临时 Session 已停止，没有需要持久化管理的 OAuth Client。'
  }
  if (target.running) return '运行中的 Runtime 正在持有 OAuth Registry；请先停止对应服务再执行撤销。'
  return '当前目标已停止，可以安全修改持久化 OAuth Registry。'
})

function formatTime(timestamp: number) {
  return new Date(timestamp * 1000).toLocaleString()
}

async function refreshTargets(preserveSelection = true) {
  const previous = selectedKey.value
  const [serverItems, gatewayItems] = await Promise.all([
    desktopApi.listServers(),
    desktopApi.listGateways(),
  ])
  servers.value = serverItems
  gateways.value = gatewayItems

  if (preserveSelection && previous && targets.value.some(item => item.key === previous)) {
    selectedKey.value = previous
    return
  }
  selectedKey.value = targets.value[0]?.key || ''
}

async function refreshClients() {
  const target = selected.value
  if (!target) {
    clients.value = []
    return
  }
  clients.value = target.kind === 'server'
    ? await desktopApi.listOAuthClients(target.serverId)
    : await desktopApi.listGatewayOAuthClients(target.gatewayId, target.serverId)
}

async function refreshAll(preserveSelection = true) {
  if (busy.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    await refreshTargets(preserveSelection)
    await refreshClients()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
  }
}

async function revokeClient(clientId: string) {
  const target = selected.value
  if (!target || !canMutate.value) return
  if (!confirm('撤销后该 AI/MCP 连接需要重新注册并授权。继续吗？')) return
  busy.value = true
  errorMessage.value = ''
  try {
    if (target.kind === 'server') {
      await desktopApi.revokeOAuthClient(target.serverId, clientId)
    } else {
      await desktopApi.revokeGatewayOAuthClient(target.gatewayId, target.serverId, clientId)
    }
    await refreshTargets(true)
    await refreshClients()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
  }
}

async function revokeAll() {
  const target = selected.value
  if (!target || !canMutate.value || !clients.value.length) return
  if (!confirm('确定撤销当前目标的全部 OAuth Client 吗？')) return
  busy.value = true
  errorMessage.value = ''
  try {
    if (target.kind === 'server') {
      await desktopApi.revokeAllOAuthClients(target.serverId)
    } else {
      await desktopApi.revokeAllGatewayOAuthClients(target.gatewayId, target.serverId)
    }
    await refreshTargets(true)
    await refreshClients()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
  }
}

async function onTargetChange(event: Event) {
  selectedKey.value = (event.target as HTMLSelectElement).value
  busy.value = true
  errorMessage.value = ''
  try {
    await refreshClients()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await refreshAll(false)
  pollTimer = window.setInterval(async () => {
    if (busy.value) return
    try {
      await refreshTargets(true)
      await refreshClients()
    } catch { /* explicit actions surface errors */ }
  }, 1500)
})

onBeforeUnmount(() => window.clearInterval(pollTimer))
</script>

<template>
  <section class="grid w-full gap-5">
    <div
      v-if="errorMessage"
      class="sticky top-2 z-30 mb-4 flex items-center justify-between gap-3 rounded-[7px] border border-destructive/25 bg-destructive/10 px-3 py-2.5 text-xs text-destructive"
    >
      <span>{{ errorMessage }}</span>
      <button class="border-0 bg-transparent text-lg leading-none text-inherit" @click="errorMessage = ''">×</button>
    </div>

    <div class="flex min-h-8 items-center justify-between gap-4">
      <div>
        <h1 class="m-0 text-xl leading-7 font-medium tracking-[-0.02em]">授权客户端</h1>
        <p class="mt-[3px] mb-0 text-xs leading-[18px] text-muted-foreground">按服务与 Workspace Profile 统一管理 Dynamic Client Registration 创建的 OAuth Client。</p>
      </div>
      <Button variant="outline" size="sm" :disabled="busy" @click="refreshAll(true)">刷新</Button>
    </div>

    <div class="mt-5 flex items-center gap-2.5 rounded-lg border border-border bg-popover px-3 py-2.5 shadow-sm">
      <label class="text-[11px] font-medium">授权目标</label>
      <select
        class="h-8 min-w-[360px] flex-1 rounded-md border border-input bg-background px-2.5 text-xs text-foreground outline-none transition focus:border-ring focus:ring-2 focus:ring-ring/20 disabled:bg-muted disabled:text-muted-foreground"
        :value="selectedKey"
        :disabled="busy || !targets.length"
        @change="onTargetChange"
      >
        <option v-for="target in targets" :key="target.key" :value="target.key">
          {{ target.label }} · {{ target.detail }} · {{ target.clientCount }} Clients
        </option>
      </select>
      <Button
        variant="outline"
        size="sm"
        class="border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive"
        :disabled="busy || !canMutate || !clients.length"
        @click="revokeAll"
      >全部撤销</Button>
    </div>

    <p class="-mt-1.5 mb-0 text-[11px] leading-[18px] text-muted-foreground">{{ mutationHelp }}</p>

    <div class="mt-2.5 overflow-auto rounded-lg border border-border bg-popover shadow-sm">
      <table class="w-full border-collapse text-[11px]">
        <thead>
          <tr>
            <th class="border-b border-border bg-muted/60 px-3 py-2.5 text-left align-middle text-[10px] font-medium whitespace-nowrap text-muted-foreground">客户端</th>
            <th class="border-b border-border bg-muted/60 px-3 py-2.5 text-left align-middle text-[10px] font-medium whitespace-nowrap text-muted-foreground">Client ID</th>
            <th class="border-b border-border bg-muted/60 px-3 py-2.5 text-left align-middle text-[10px] font-medium whitespace-nowrap text-muted-foreground">Redirect URI</th>
            <th class="border-b border-border bg-muted/60 px-3 py-2.5 text-left align-middle text-[10px] font-medium whitespace-nowrap text-muted-foreground">认证方式</th>
            <th class="border-b border-border bg-muted/60 px-3 py-2.5 text-left align-middle text-[10px] font-medium whitespace-nowrap text-muted-foreground">注册时间</th>
            <th class="border-b border-border bg-muted/60 px-3 py-2.5"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="client in clients" :key="client.client_id" class="hover:bg-secondary/40">
            <td class="border-b border-border px-3 py-2.5 text-left align-middle">{{ client.client_name }}</td>
            <td class="border-b border-border px-3 py-2.5 text-left align-middle font-mono">{{ client.client_id }}</td>
            <td class="border-b border-border px-3 py-2.5 text-left align-middle font-mono text-[10px] whitespace-pre-line">{{ client.redirect_uris.join('\n') }}</td>
            <td class="border-b border-border px-3 py-2.5 text-left align-middle">{{ client.token_endpoint_auth_method }}</td>
            <td class="border-b border-border px-3 py-2.5 text-left align-middle">{{ formatTime(client.issued_at) }}</td>
            <td class="border-b border-border px-3 py-2.5 text-left align-middle">
              <Button
                variant="ghost"
                size="sm"
                class="rounded-full bg-destructive/10 text-destructive hover:bg-destructive/15 hover:text-destructive"
                :disabled="busy || !canMutate"
                @click="revokeClient(client.client_id)"
              >撤销</Button>
            </td>
          </tr>
          <tr v-if="!clients.length">
            <td colspan="6" class="px-3 py-11 text-center text-muted-foreground">当前授权目标还没有可显示的 OAuth Client。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

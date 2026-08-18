<script setup lang="ts">
import { LoaderCircle, Play, Plus, Square } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import type { ServerDto } from '../types'

defineProps<{ servers: ServerDto[]; selectedId: string; startingId: string }>()
const emit = defineEmits<{
  select: [serverId: string]
  toggle: [server: ServerDto]
  create: []
}>()

function providerName(server: ServerDto) {
  const names: Record<ServerDto['network']['provider'], string> = {
    cloudflare: 'Cloudflare',
    frp: 'FRP',
    ngrok: 'ngrok',
    tailscale: 'Tailscale',
    external: 'External',
  }
  return names[server.network.provider]
}
</script>

<template>
  <section class="server-list-panel">
    <div class="panel-toolbar">
      <div>
        <h2>服务列表</h2>
        <p>{{ servers.length }} 个 Server Profile</p>
      </div>
      <Button size="sm" class="primary-action-button" @click="emit('create')">
        <Plus :size="14" />
        新建
      </Button>
    </div>

    <div v-if="servers.length" class="server-row-list">
      <article
        v-for="server in servers"
        :key="server.server_id"
        :class="['server-row', { selected: server.server_id === selectedId }]"
        role="button"
        tabindex="0"
        @click="emit('select', server.server_id)"
        @keydown.enter="emit('select', server.server_id)"
        @keydown.space.prevent="emit('select', server.server_id)"
      >
        <span :class="['server-status-dot', server.server_id === startingId ? 'starting' : server.running ? 'running' : server.exit_reason ? 'error' : 'idle']" />
        <span class="server-row-main">
          <span class="server-row-title">
            <strong>{{ server.name }}</strong>
            <span>{{ providerName(server) }}</span>
          </span>
          <span class="server-row-path mono truncate">{{ server.workspace }}</span>
          <span class="server-row-meta">
            <span>127.0.0.1:{{ server.port }}</span>
            <span>{{ server.lifecycle === 'ephemeral' ? '临时 Session' : '持久服务' }}</span>
            <span>{{ server.oauth_client_count }} OAuth</span>
          </span>
        </span>
        <Button
          size="icon"
          :class="[
            'server-toggle',
            server.running ? 'server-toggle-running' : 'server-toggle-idle',
            { 'server-toggle-starting': server.server_id === startingId },
          ]"
          :disabled="server.server_id === startingId"
          :title="server.server_id === startingId ? '正在启动服务' : server.running ? '停止服务' : '启动服务'"
          @click.stop="emit('toggle', server)"
        >
          <LoaderCircle v-if="server.server_id === startingId" class="server-toggle-spinner" :size="14" />
          <Square v-else-if="server.running" :size="14" />
          <Play v-else :size="14" />
        </Button>
      </article>
    </div>

    <div v-else class="empty-state compact-empty">
      <strong>还没有 MCP Server</strong>
      <p>创建第一个服务后，默认端口从 8234 开始。</p>
      <Button size="sm" class="primary-action-button" @click="emit('create')"><Plus :size="14" /> 新建服务</Button>
    </div>
  </section>
</template>

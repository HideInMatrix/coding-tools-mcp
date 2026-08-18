<script setup lang="ts">
import type { OAuthClientDto, ServerDto } from '../types'

defineProps<{ servers: ServerDto[]; selectedId: string; clients: OAuthClientDto[] }>()
const emit = defineEmits<{
  select: [serverId: string]
  refresh: []
  revoke: [clientId: string]
  revokeAll: []
}>()

function formatTime(timestamp: number) {
  return new Date(timestamp * 1000).toLocaleString()
}

function onServerChange(event: Event) {
  const target = event.target as HTMLSelectElement
  emit('select', target.value)
}
</script>

<template>
  <section class="content-page">
    <div class="page-title-row">
      <div><h1>授权客户端</h1><p>管理当前 MCP Server 通过 Dynamic Client Registration 创建的 OAuth Client。</p></div>
      <button class="secondary-button" @click="emit('refresh')">刷新</button>
    </div>
    <div class="toolbar-card">
      <label>当前 Server</label>
      <select :value="selectedId" @change="onServerChange">
        <option v-for="server in servers" :key="server.server_id" :value="server.server_id">{{ server.name }} · {{ server.port }}</option>
      </select>
      <button class="danger-button" :disabled="!clients.length || servers.find(s => s.server_id === selectedId)?.running" @click="emit('revokeAll')">全部撤销</button>
    </div>
    <div class="table-card">
      <table>
        <thead><tr><th>客户端</th><th>Client ID</th><th>Redirect URI</th><th>认证方式</th><th>注册时间</th><th></th></tr></thead>
        <tbody>
          <tr v-for="client in clients" :key="client.client_id">
            <td>{{ client.client_name }}</td>
            <td class="mono">{{ client.client_id }}</td>
            <td class="mono small">{{ client.redirect_uris.join('\n') }}</td>
            <td>{{ client.token_endpoint_auth_method }}</td>
            <td>{{ formatTime(client.issued_at) }}</td>
            <td><button class="text-danger-button" :disabled="servers.find(s => s.server_id === selectedId)?.running" @click="emit('revoke', client.client_id)">撤销</button></td>
          </tr>
          <tr v-if="!clients.length"><td colspan="6" class="empty-cell">当前 Server 还没有可显示的 OAuth Client。</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { desktopApi } from '../api/desktop'
import type { ServerDraft } from '../types'

const model = defineModel<ServerDraft>({ required: true })
defineProps<{ isNew: boolean; running: boolean }>()
const emit = defineEmits<{ save: []; delete: []; start: []; stop: [] }>()

const provider = computed(() => model.value.network.provider)

async function chooseWorkspace() {
  const value = await desktopApi.chooseWorkspace(model.value.workspace)
  if (value) model.value.workspace = value
}

async function chooseConfigFile() {
  const value = await desktopApi.chooseFile(model.value.network.options.config_file || '')
  if (value) model.value.network.options.config_file = value
}

async function autoDetect(product: string, option = 'executable') {
  const candidate = await desktopApi.detectExecutable(product, model.value.network.options[option] || '')
  model.value.network.options[option] = candidate.path
}
</script>

<template>
  <section class="editor-panel">
    <div class="section-heading">
      <div>
        <h2>{{ isNew ? '新建 MCP Server' : '服务设置' }}</h2>
        <p>每个 Server 拥有独立端口、网络入口和 OAuth Registry。</p>
      </div>
      <span :class="['status-pill', running ? 'running' : 'stopped']">{{ running ? '运行中' : '已停止' }}</span>
    </div>

    <div class="form-grid">
      <label class="field span-2">
        <span>服务名称</span>
        <input v-model.trim="model.name" :disabled="running" placeholder="例如：公司项目" />
      </label>
      <label class="field span-2">
        <span>Workspace</span>
        <div class="input-action">
          <input v-model.trim="model.workspace" :disabled="running" placeholder="选择需要授权给 MCP 的项目目录" />
          <button type="button" :disabled="running" @click="chooseWorkspace">选择</button>
        </div>
      </label>
      <label class="field">
        <span>本地端口</span>
        <input v-model.number="model.port" :disabled="running" type="number" min="1" max="65535" />
      </label>
      <label class="field">
        <span>监听地址</span>
        <input v-model.trim="model.host" disabled />
      </label>
      <label class="field span-2">
        <span>OAuth Password</span>
        <input v-model="model.oauth_password" type="password" placeholder="OAuth 授权页登录密码" />
      </label>
      <label class="check-field span-2">
        <input v-model="model.remember_secrets" type="checkbox" />
        <span>在本机持久化 OAuth Password、Tunnel Token 等敏感凭据</span>
      </label>
      <label class="field span-2">
        <span>网络方案</span>
        <select v-model="model.network.provider" :disabled="running">
          <option value="cloudflare">Cloudflare Tunnel</option>
          <option value="frp">FRP</option>
          <option value="ngrok">ngrok</option>
          <option value="tailscale">Tailscale Funnel</option>
          <option value="external">自定义公网 URL</option>
        </select>
      </label>

      <template v-if="provider === 'cloudflare'">
        <label class="field span-2">
          <span>Public URL</span>
          <input v-model.trim="model.network.public_url" :disabled="running" placeholder="留空使用 Quick Tunnel；固定域名例如 https://mcp.example.com" />
        </label>
        <label class="field span-2">
          <span>Tunnel Token</span>
          <input v-model="model.network.options.tunnel_token" :disabled="running" type="password" placeholder="固定 Public URL 时填写 Named Tunnel Token" />
        </label>
        <p class="form-note span-2">Public URL 和 Tunnel Token 都留空时，本次运行使用临时 Quick Tunnel，停止后公网域名和 OAuth Session 一并销毁。</p>
      </template>

      <template v-else-if="provider === 'frp'">
        <label class="field span-2"><span>Public URL</span><input v-model.trim="model.network.public_url" :disabled="running" /></label>
        <label class="field span-2">
          <span>frpc</span>
          <div class="input-action"><input v-model.trim="model.network.options.executable" :disabled="running" /><button type="button" :disabled="running" @click="autoDetect('frpc')">检测</button></div>
        </label>
        <label class="field span-2">
          <span>FRP Config</span>
          <div class="input-action"><input v-model.trim="model.network.options.config_file" :disabled="running" /><button type="button" :disabled="running" @click="chooseConfigFile">选择</button></div>
        </label>
      </template>

      <template v-else-if="provider === 'ngrok'">
        <label class="field span-2"><span>Public URL</span><input v-model.trim="model.network.public_url" :disabled="running" placeholder="可选固定域名" /></label>
        <label class="field span-2">
          <span>ngrok</span>
          <div class="input-action"><input v-model.trim="model.network.options.executable" :disabled="running" /><button type="button" :disabled="running" @click="autoDetect('ngrok')">检测</button></div>
        </label>
        <label class="field span-2"><span>Auth Token</span><input v-model="model.network.options.authtoken" :disabled="running" type="password" /></label>
      </template>

      <template v-else-if="provider === 'tailscale'">
        <label class="field span-2">
          <span>tailscale</span>
          <div class="input-action"><input v-model.trim="model.network.options.executable" :disabled="running" /><button type="button" :disabled="running" @click="autoDetect('tailscale')">检测</button></div>
        </label>
      </template>

      <template v-else>
        <label class="field span-2"><span>Public URL</span><input v-model.trim="model.network.public_url" :disabled="running" placeholder="例如 https://mcp.example.com" /></label>
      </template>
    </div>

    <div class="editor-actions">
      <button v-if="!isNew" class="danger-button" :disabled="running" @click="emit('delete')">删除服务</button>
      <div class="action-spacer" />
      <button class="secondary-button" :disabled="running" @click="emit('save')">{{ isNew ? '创建服务' : '保存配置' }}</button>
      <button v-if="running" class="danger-solid-button" @click="emit('stop')">停止 MCP</button>
      <button v-else-if="!isNew" class="primary-button" @click="emit('start')">启动 MCP</button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Check, Copy, LoaderCircle } from '@lucide/vue'
import { desktopApi } from '../api/desktop'
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from '@/components/ui/input-group'
import type { ServerDraft } from '../types'

const model = defineModel<ServerDraft>({ required: true })
const props = defineProps<{
  isNew: boolean
  running: boolean
  starting: boolean
  publicMcpUrl: string
}>()
const emit = defineEmits<{ save: []; delete: []; start: []; stop: [] }>()

const provider = computed(() => model.value.network.provider)
const locked = computed(() => props.running || props.starting)
const copied = ref(false)

function stripMcpSuffix(value: string): string {
  const normalized = value.trim().replace(/\/+$/, '')
  return normalized.toLowerCase().endsWith('/mcp') ? normalized.slice(0, -4) : normalized
}

function withMcpSuffix(value: string): string {
  const base = stripMcpSuffix(value)
  return base ? `${base}/mcp` : ''
}

const publicUrlBase = computed({
  get() {
    if (props.publicMcpUrl) return stripMcpSuffix(props.publicMcpUrl)
    return stripMcpSuffix(model.value.network.public_url)
  },
  set(value: string) {
    if (locked.value || provider.value === 'tailscale') return
    model.value.network.public_url = stripMcpSuffix(value)
  },
})

const resolvedPublicMcpUrl = computed(() => {
  if (props.publicMcpUrl) return withMcpSuffix(props.publicMcpUrl)
  return withMcpSuffix(model.value.network.public_url)
})

const publicUrlPlaceholder = computed(() => {
  if (provider.value === 'cloudflare') return '留空使用 Quick Tunnel；固定域名例如 https://mcp.example.com'
  if (provider.value === 'ngrok') return '可选固定域名；留空由 ngrok 启动后生成'
  if (provider.value === 'tailscale') return '启动后自动显示 Tailscale Funnel URL'
  return '例如 https://mcp.example.com'
})

async function copyMcpUrl() {
  const value = resolvedPublicMcpUrl.value
  if (!value) return
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = value
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      textarea.remove()
    }
    copied.value = true
    window.setTimeout(() => { copied.value = false }, 1600)
  } catch {
    copied.value = false
  }
}

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
      <span :class="['status-pill', starting ? 'starting' : running ? 'running' : 'stopped']">
        <LoaderCircle v-if="starting" class="status-spinner" :size="12" />
        {{ starting ? '启动中…' : running ? '运行中' : '已停止' }}
      </span>
    </div>

    <div class="form-grid">
      <label class="field span-2">
        <span>服务名称</span>
        <input v-model.trim="model.name" :disabled="locked" placeholder="例如：公司项目" />
      </label>
      <label class="field span-2">
        <span>Workspace</span>
        <div class="input-action">
          <input v-model.trim="model.workspace" :disabled="locked" placeholder="选择需要授权给 MCP 的项目目录" />
          <button type="button" :disabled="locked" @click="chooseWorkspace">选择</button>
        </div>
      </label>
      <label class="field">
        <span>本地端口</span>
        <input v-model.number="model.port" :disabled="locked" type="number" min="1" max="65535" />
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
        <select v-model="model.network.provider" :disabled="locked">
          <option value="cloudflare">Cloudflare Tunnel</option>
          <option value="frp">FRP</option>
          <option value="ngrok">ngrok</option>
          <option value="tailscale">Tailscale Funnel</option>
          <option value="external">自定义公网 URL</option>
        </select>
      </label>

      <label class="field span-2">
        <span>权限模式</span>
        <select v-model="model.permission_mode" :disabled="locked">
          <option value="safe">安全 Safe（推荐）</option>
          <option value="trusted">信任 Trusted</option>
          <option value="dangerous">危险 Dangerous</option>
        </select>
        <span v-if="model.permission_mode === 'safe'" class="field-help">启用最严格的 Workspace、环境变量和 OS 沙箱限制；客户端支持授权交互时，可对受限操作申请临时权限。</span>
        <span v-else-if="model.permission_mode === 'trusted'" class="field-help">允许网络等常用开发能力，但仍保留 Workspace 与 OS 沙箱边界。</span>
        <span v-else class="permission-warning">危险模式会关闭 OS 进程沙箱并继承完整用户环境，仅在你明确需要完整终端权限时使用。</span>
      </label>

      <label class="field span-2">
        <span>Public URL</span>
        <InputGroup>
          <InputGroupInput
            v-model="publicUrlBase"
            :disabled="locked || provider === 'tailscale'"
            :placeholder="publicUrlPlaceholder"
          />
          <InputGroupAddon>/mcp</InputGroupAddon>
          <InputGroupButton
            v-if="running && resolvedPublicMcpUrl"
            :title="copied ? '已复制 MCP 地址' : `复制 ${resolvedPublicMcpUrl}`"
            :aria-label="copied ? '已复制 MCP 地址' : '复制 MCP 地址'"
            @click="copyMcpUrl"
          >
            <Check v-if="copied" :size="14" />
            <Copy v-else :size="14" />
          </InputGroupButton>
        </InputGroup>
        <span v-if="running && resolvedPublicMcpUrl" class="field-help">复制后可直接粘贴到 ChatGPT / Claude 的 MCP Server URL。</span>
      </label>

      <template v-if="provider === 'cloudflare'">
        <label class="field span-2">
          <span>Tunnel Token</span>
          <input v-model="model.network.options.tunnel_token" :disabled="locked" type="password" placeholder="固定 Public URL 时填写 Named Tunnel Token" />
        </label>
        <p class="form-note span-2">Public URL 和 Tunnel Token 都留空时，本次运行使用临时 Quick Tunnel，停止后公网域名和 OAuth Session 一并销毁。</p>
      </template>

      <template v-else-if="provider === 'frp'">
        <label class="field span-2">
          <span>frpc</span>
          <div class="input-action"><input v-model.trim="model.network.options.executable" :disabled="locked" /><button type="button" :disabled="locked" @click="autoDetect('frpc')">检测</button></div>
        </label>
        <label class="field span-2">
          <span>FRP Config</span>
          <div class="input-action"><input v-model.trim="model.network.options.config_file" :disabled="locked" /><button type="button" :disabled="locked" @click="chooseConfigFile">选择</button></div>
        </label>
      </template>

      <template v-else-if="provider === 'ngrok'">
        <label class="field span-2">
          <span>ngrok</span>
          <div class="input-action"><input v-model.trim="model.network.options.executable" :disabled="locked" /><button type="button" :disabled="locked" @click="autoDetect('ngrok')">检测</button></div>
        </label>
        <label class="field span-2"><span>Auth Token</span><input v-model="model.network.options.authtoken" :disabled="locked" type="password" /></label>
      </template>

      <template v-else-if="provider === 'tailscale'">
        <label class="field span-2">
          <span>tailscale</span>
          <div class="input-action"><input v-model.trim="model.network.options.executable" :disabled="locked" /><button type="button" :disabled="locked" @click="autoDetect('tailscale')">检测</button></div>
        </label>
      </template>
    </div>

    <div class="editor-actions">
      <button v-if="!isNew" class="danger-button" :disabled="locked" @click="emit('delete')">删除服务</button>
      <div class="action-spacer" />
      <button class="secondary-button" :disabled="locked" @click="emit('save')">{{ isNew ? '创建服务' : '保存配置' }}</button>
      <button v-if="running" class="danger-solid-button" @click="emit('stop')">停止 MCP</button>
      <button v-else-if="!isNew" class="primary-button" :disabled="starting" @click="emit('start')">
        <LoaderCircle v-if="starting" class="status-spinner" :size="14" />
        {{ starting ? '启动中…' : '启动 MCP' }}
      </button>
    </div>
  </section>
</template>

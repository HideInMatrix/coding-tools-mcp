<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  CheckCircle2,
  Plus,
  RefreshCw,
  Save,
  Server,
  Trash2,
} from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { desktopApi } from '../api/desktop'
import type {
  CapabilityCatalogDto,
  MCPConnectionDefinitionDto,
} from '../types'

const catalog = ref<CapabilityCatalogDto | null>(null)
const selectedId = ref('')
const draft = ref<MCPConnectionDefinitionDto>(emptyConnection())
const argumentsText = ref('[]')
const environmentText = ref('{}')
const environmentRefsText = ref('{}')
const headersText = ref('{}')
const headerRefsText = ref('{}')
const busy = ref(false)
const error = ref('')
const notice = ref('')

function emptyConnection(): MCPConnectionDefinitionDto {
  return {
    schema_version: 1,
    id: '',
    name: '',
    transport: 'http',
    endpoint: '',
    command: '',
    enabled: true,
    version: 1,
    tool_count: 0,
    last_discovered_at: 0,
    last_error: '',
    scope: 'global',
    arguments: [],
    environment: {},
    environment_refs: {},
    headers: {},
    header_refs: {},
    tools: [],
  }
}

const selectedSummary = computed(
  () => catalog.value?.mcp_connections.find(item => item.id === selectedId.value) ?? null,
)
const canDelete = computed(() => Boolean(selectedId.value))
const canProbe = computed(() => Boolean(selectedId.value && draft.value.enabled))

function stringify(value: unknown) {
  return JSON.stringify(value, null, 2)
}

function applyConnection(value: MCPConnectionDefinitionDto) {
  draft.value = { ...value }
  selectedId.value = value.id
  argumentsText.value = stringify(value.arguments ?? [])
  environmentText.value = stringify(value.environment ?? {})
  environmentRefsText.value = stringify(value.environment_refs ?? {})
  headersText.value = stringify(value.headers ?? {})
  headerRefsText.value = stringify(value.header_refs ?? {})
}

function parseObject(text: string, label: string): Record<string, string> {
  const value = JSON.parse(text)
  if (!value || Array.isArray(value) || typeof value !== 'object') {
    throw new Error(`${label} 必须是 JSON object。`)
  }
  return value as Record<string, string>
}

function definition(): MCPConnectionDefinitionDto {
  const args = JSON.parse(argumentsText.value)
  if (!Array.isArray(args) || args.some(item => typeof item !== 'string')) {
    throw new Error('Arguments 必须是 string[]。')
  }
  return {
    ...draft.value,
    arguments: args,
    environment: parseObject(environmentText.value, 'Environment'),
    environment_refs: parseObject(environmentRefsText.value, 'Environment Refs'),
    headers: parseObject(headersText.value, 'Headers'),
    header_refs: parseObject(headerRefsText.value, 'Header Refs'),
  }
}

async function refreshCatalog(preferredId = selectedId.value) {
  catalog.value = await desktopApi.capabilityCatalog()
  if (preferredId && catalog.value.mcp_connections.some(item => item.id === preferredId)) {
    await selectConnection(preferredId)
  } else if (catalog.value.mcp_connections[0]) {
    await selectConnection(catalog.value.mcp_connections[0].id)
  } else {
    newConnection()
  }
}

async function load() {
  busy.value = true
  error.value = ''
  try {
    await refreshCatalog('')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

async function selectConnection(connectionId: string) {
  if (!connectionId) return
  busy.value = true
  error.value = ''
  try {
    applyConnection(await desktopApi.workbenchMCPConnection(connectionId))
    notice.value = ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

function newConnection() {
  selectedId.value = ''
  draft.value = emptyConnection()
  argumentsText.value = '[]'
  environmentText.value = '{}'
  environmentRefsText.value = '{}'
  headersText.value = '{}'
  headerRefsText.value = '{}'
  error.value = ''
  notice.value = '新 MCP 服务尚未保存。'
}

async function validateConnection() {
  busy.value = true
  error.value = ''
  try {
    const result = await desktopApi.validateWorkbenchMCPConnection(definition())
    notice.value = result.ok ? 'MCP 服务配置验证通过。' : 'MCP 服务配置验证失败。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

async function saveConnection() {
  busy.value = true
  error.value = ''
  try {
    const value = definition()
    const current = catalog.value?.mcp_connections.find(item => item.id === value.id)
    const result = await desktopApi.saveWorkbenchMCPConnection(
      value,
      current?.version ?? 0,
    )
    applyConnection(result.connection)
    await refreshCatalog(result.connection.id)
    notice.value = `MCP 服务已保存为 v${result.connection.version}。`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

async function deleteConnection() {
  if (!canDelete.value) return
  if (!window.confirm(`删除 MCP 服务“${draft.value.name || selectedId.value}”？`)) return
  busy.value = true
  error.value = ''
  try {
    const deletedId = selectedId.value
    if (await desktopApi.deleteWorkbenchMCPConnection(deletedId)) {
      selectedId.value = ''
      await refreshCatalog('')
      notice.value = `MCP 服务 ${deletedId} 已删除。`
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

async function testConnection() {
  if (!canProbe.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await desktopApi.testWorkbenchMCPConnection(selectedId.value)
    if (!result.ok) throw new Error(result.error || 'MCP 服务连接失败。')
    notice.value = `连接成功 · MCP ${result.protocol_version || 'unknown'} · ${result.elapsed_ms}ms`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

async function discoverTools() {
  if (!canProbe.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await desktopApi.discoverWorkbenchMCPConnectionTools(selectedId.value)
    if (result.connection) {
      applyConnection(result.connection)
      await refreshCatalog(result.connection.id)
    }
    if (!result.ok || !result.connection) {
      throw new Error(result.error || 'MCP Tool Discovery 失败。')
    }
    notice.value = `已发现 ${result.connection.tool_count} 个 MCP Tools。`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="flex min-h-0 w-full flex-1 flex-col gap-3">
    <header class="flex items-start justify-between gap-4">
      <div>
        <h1 class="m-0 text-xl font-medium tracking-[-0.02em]">MCP 服务</h1>
        <p class="mt-1 mb-0 text-xs leading-[18px] text-muted-foreground">
          管理全局外部 MCP 能力来源。连接成功后可发现其 Tools，供 Skill 和 Workflow 引用。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <Button variant="outline" size="sm" :disabled="busy" @click="refreshCatalog()">
          <RefreshCw :size="14" />刷新
        </Button>
        <Button variant="outline" size="sm" :disabled="busy" @click="newConnection">
          <Plus :size="14" />新建
        </Button>
      </div>
    </header>

    <div v-if="error" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
      {{ error }}
    </div>
    <div v-if="notice" class="rounded-md border border-border bg-secondary/40 px-3 py-2 text-xs text-muted-foreground">
      {{ notice }}
    </div>

    <div class="grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)] overflow-hidden rounded-lg border border-border bg-card">
      <aside class="min-h-0 overflow-y-auto border-r border-border p-2">
        <button
          v-for="connection in catalog?.mcp_connections ?? []"
          :key="connection.id"
          type="button"
          :class="[
            'mb-1 w-full justify-start rounded-md px-2.5 py-2 text-left transition-colors',
            selectedId === connection.id ? 'bg-secondary' : 'hover:bg-secondary/60',
          ]"
          @click="selectConnection(connection.id)"
        >
          <div class="min-w-0 flex-1 truncate text-xs font-medium">{{ connection.name }}</div>
        </button>
      </aside>

      <main class="min-h-0 overflow-y-auto p-4">
        <div class="grid max-w-4xl gap-4">
          <div class="grid grid-cols-2 gap-3">
            <label class="field">
              <span>MCP ID</span>
              <input v-model="draft.id" :disabled="Boolean(selectedId)" placeholder="github" />
            </label>
            <label class="field">
              <span>名称</span>
              <input v-model="draft.name" placeholder="GitHub MCP" />
            </label>
          </div>

          <div class="grid grid-cols-2 gap-3">
            <label class="field">
              <span>Transport</span>
              <select v-model="draft.transport">
                <option value="http">HTTP</option>
                <option value="stdio">stdio</option>
              </select>
            </label>
            <label class="flex items-center gap-2 pt-6 text-xs">
              <input v-model="draft.enabled" type="checkbox" />
              <span>启用此 MCP 服务</span>
            </label>
          </div>

          <label v-if="draft.transport === 'http'" class="field">
            <span>Endpoint</span>
            <input v-model.trim="draft.endpoint" placeholder="https://example.com/mcp" />
          </label>

          <template v-else>
            <label class="field">
              <span>Command</span>
              <input v-model.trim="draft.command" placeholder="/path/to/mcp-server" />
            </label>
            <label class="field">
              <span>Arguments JSON</span>
              <textarea v-model="argumentsText" class="min-h-20 resize-y font-mono" spellcheck="false" />
            </label>
            <label class="field">
              <span>Environment JSON（仅非敏感值）</span>
              <textarea v-model="environmentText" class="min-h-20 resize-y font-mono" spellcheck="false" />
            </label>
            <label class="field">
              <span>Environment Refs JSON</span>
              <textarea v-model="environmentRefsText" class="min-h-20 resize-y font-mono" spellcheck="false" />
              <small class="text-[10px] font-normal text-muted-foreground">敏感值使用引用，例如 { "API_KEY": "env:GITHUB_TOKEN" }。</small>
            </label>
          </template>

          <template v-if="draft.transport === 'http'">
            <label class="field">
              <span>Headers JSON（仅非敏感值）</span>
              <textarea v-model="headersText" class="min-h-20 resize-y font-mono" spellcheck="false" />
            </label>
            <label class="field">
              <span>Header Refs JSON</span>
              <textarea v-model="headerRefsText" class="min-h-20 resize-y font-mono" spellcheck="false" />
              <small class="text-[10px] font-normal text-muted-foreground">例如 { "Authorization": "env:GITHUB_MCP_AUTH" }，不会把真实 Secret 写入资产文件。</small>
            </label>
          </template>

          <div class="grid gap-2">
            <div class="flex items-center justify-between">
              <span class="text-[11px] font-medium">Discovered Tools</span>
              <span class="text-[10px] text-muted-foreground">{{ draft.tools.length }} Tools</span>
            </div>
            <div class="max-h-64 overflow-y-auto rounded-md border border-border bg-background">
              <div
                v-for="tool in draft.tools"
                :key="tool.name"
                class="border-b border-border px-3 py-2 last:border-b-0"
              >
                <div class="font-mono text-[11px] font-medium">{{ tool.name }}</div>
                <div v-if="tool.description" class="mt-0.5 text-[10px] leading-4 text-muted-foreground">
                  {{ tool.description }}
                </div>
              </div>
              <div v-if="!draft.tools.length" class="px-3 py-6 text-center text-[10px] text-muted-foreground">
                保存连接后点击“发现 Tools”。
              </div>
            </div>
          </div>

          <div class="flex items-center justify-between border-t border-border pt-3">
            <div class="text-[10px] text-muted-foreground">
              {{ selectedSummary ? `global · v${selectedSummary.version}` : 'global · unsaved' }}
              <span v-if="draft.last_error" class="ml-2 text-destructive">{{ draft.last_error }}</span>
            </div>
            <div class="flex flex-wrap items-center justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                class="text-destructive hover:bg-destructive/10 hover:text-destructive"
                :disabled="busy || !canDelete"
                @click="deleteConnection"
              >
                <Trash2 :size="14" />删除
              </Button>
              <Button variant="outline" size="sm" :disabled="busy" @click="validateConnection">
                <CheckCircle2 :size="14" />验证
              </Button>
              <Button variant="outline" size="sm" :disabled="busy || !canProbe" @click="testConnection">
                <Server :size="14" />测试连接
              </Button>
              <Button variant="outline" size="sm" :disabled="busy || !canProbe" @click="discoverTools">
                <RefreshCw :size="14" />发现 Tools
              </Button>
              <Button size="sm" :disabled="busy" @click="saveConnection">
                <Save :size="14" />保存
              </Button>
            </div>
          </div>
        </div>
      </main>
    </div>
  </section>
</template>

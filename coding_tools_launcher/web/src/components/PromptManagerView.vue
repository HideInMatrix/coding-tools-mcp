<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { CheckCircle2, Plus, RefreshCw, Save, Trash2 } from '@lucide/vue'
import { desktopApi } from '../api/desktop'
import { Button } from '@/components/ui/button'
import type {
  CapabilityCatalogDto,
  PromptDefinitionDto,
} from '../types'

const catalog = ref<CapabilityCatalogDto | null>(null)
const selectedId = ref('')
const draft = ref<PromptDefinitionDto>(emptyPrompt())
const argumentsText = ref('[]')
const messagesText = ref('[\n  {"role":"user","content":""}\n]')
const busy = ref(false)
const error = ref('')
const notice = ref('')

function emptyPrompt(): PromptDefinitionDto {
  return {
    schema_version: 1,
    id: '',
    name: '',
    description: '',
    version: 1,
    scope: 'global',
    arguments: [],
    messages: [{ role: 'user', content: '' }],
  }
}

const selectedSummary = computed(
  () => catalog.value?.prompts.find(item => item.id === selectedId.value) ?? null,
)
const canDelete = computed(() => selectedSummary.value?.scope === 'global')

function applyPrompt(value: PromptDefinitionDto) {
  draft.value = { ...value }
  selectedId.value = value.id
  argumentsText.value = JSON.stringify(value.arguments ?? [], null, 2)
  messagesText.value = JSON.stringify(value.messages ?? [], null, 2)
}

async function refreshCatalog(preferredId = selectedId.value) {
  catalog.value = await desktopApi.capabilityCatalog()
  if (preferredId && catalog.value.prompts.some(item => item.id === preferredId)) {
    await selectPrompt(preferredId)
  } else if (catalog.value.prompts[0]) {
    await selectPrompt(catalog.value.prompts[0].id)
  } else {
    newPrompt()
  }
}

async function selectPrompt(promptId: string) {
  if (!promptId) return
  busy.value = true
  error.value = ''
  try {
    applyPrompt(await desktopApi.workbenchPrompt(promptId))
    notice.value = ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

function newPrompt() {
  selectedId.value = ''
  draft.value = emptyPrompt()
  argumentsText.value = '[]'
  messagesText.value = '[\n  {"role":"user","content":""}\n]'
  error.value = ''
  notice.value = '新 Prompt 尚未保存。'
}

function definition(): PromptDefinitionDto {
  const args = JSON.parse(argumentsText.value)
  const messages = JSON.parse(messagesText.value)
  if (!Array.isArray(args)) throw new Error('Arguments 必须是 JSON array。')
  if (!Array.isArray(messages)) throw new Error('Messages 必须是 JSON array。')
  return {
    ...draft.value,
    arguments: args,
    messages,
  }
}

async function validatePrompt() {
  busy.value = true
  error.value = ''
  try {
    const result = await desktopApi.validateWorkbenchPrompt(definition())
    notice.value = result.ok ? 'Prompt 验证通过。' : 'Prompt 验证失败。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

async function savePrompt() {
  busy.value = true
  error.value = ''
  try {
    const value = definition()
    const current = catalog.value?.prompts.find(item => item.id === value.id)
    const expectedVersion = current?.scope === 'global' ? current.version : 0
    const result = await desktopApi.saveWorkbenchPrompt(
      value,
      expectedVersion,
    )
    applyPrompt(result.prompt)
    await refreshCatalog(result.prompt.id)
    notice.value = `Prompt 已保存为 v${result.prompt.version}。`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

async function deletePrompt() {
  if (!selectedId.value || !canDelete.value) return
  if (!window.confirm(`删除 Global Prompt “${draft.value.name || selectedId.value}”？`)) return
  busy.value = true
  error.value = ''
  try {
    const deleted = await desktopApi.deleteWorkbenchPrompt(selectedId.value)
    if (deleted) {
      const deletedId = selectedId.value
      selectedId.value = ''
      await refreshCatalog('')
      notice.value = `Prompt ${deletedId} 已删除。`
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

onMounted(() => refreshCatalog())
</script>

<template>
  <section class="flex min-h-0 w-full flex-1 flex-col gap-3">
    <header class="flex items-start justify-between gap-4">
      <div>
        <h1 class="m-0 text-xl font-medium tracking-[-0.02em]">Prompts</h1>
        <p class="mt-1 mb-0 text-xs leading-[18px] text-muted-foreground">
          管理可复用模型指令模板。Prompt 不等于宿主 AI 的 System Prompt。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <Button variant="outline" size="sm" :disabled="busy" @click="refreshCatalog()">
          <RefreshCw :size="14" />刷新
        </Button>
        <Button variant="outline" size="sm" :disabled="busy" @click="newPrompt">
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
          v-for="prompt in catalog?.prompts ?? []"
          :key="prompt.id"
          type="button"
          :class="[
            'mb-1 w-full justify-start rounded-md px-2.5 py-2 text-left transition-colors',
            selectedId === prompt.id ? 'bg-secondary' : 'hover:bg-secondary/60',
          ]"
          @click="selectPrompt(prompt.id)"
        >
          <span class="truncate text-xs font-medium">{{ prompt.name }}</span>
        </button>
      </aside>

      <main class="min-h-0 overflow-y-auto p-4">
        <div class="grid max-w-4xl gap-4">
          <div class="grid grid-cols-2 gap-3">
            <label class="field">
              <span>Prompt ID</span>
              <input v-model="draft.id" :disabled="Boolean(selectedId)" placeholder="frontend-review" />
            </label>
            <label class="field">
              <span>名称</span>
              <input v-model="draft.name" placeholder="Frontend Review" />
            </label>
          </div>
          <label class="field">
            <span>说明</span>
            <input v-model="draft.description" placeholder="告诉 AI 这个 Prompt 适合什么任务" />
          </label>
          <label class="field">
            <span>Arguments JSON</span>
            <textarea v-model="argumentsText" class="min-h-28 resize-y" spellcheck="false" />
          </label>
          <label class="field">
            <span>Messages JSON</span>
            <textarea v-model="messagesText" class="min-h-44 resize-y" spellcheck="false" />
          </label>

          <div class="flex items-center justify-between border-t border-border pt-3">
            <div class="text-[10px] text-muted-foreground">
              {{ selectedSummary ? `${selectedSummary.scope} · v${selectedSummary.version}` : 'global · unsaved' }}
            </div>
            <div class="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                class="text-destructive hover:bg-destructive/10 hover:text-destructive"
                :disabled="busy || !canDelete"
                @click="deletePrompt"
              >
                <Trash2 :size="14" />删除
              </Button>
              <Button variant="outline" size="sm" :disabled="busy" @click="validatePrompt">
                <CheckCircle2 :size="14" />验证
              </Button>
              <Button size="sm" :disabled="busy" @click="savePrompt">
                <Save :size="14" />保存
              </Button>
            </div>
          </div>
        </div>
      </main>
    </div>
  </section>
</template>

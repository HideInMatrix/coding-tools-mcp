<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { CheckCircle2, Plus, RefreshCw, Save, Trash2 } from '@lucide/vue'
import { desktopApi } from '../api/desktop'
import { Button } from '@/components/ui/button'
import type {
  CapabilityCatalogDto,
  SkillDefinitionDto,
  ToolReferenceDto,
} from '../types'

const catalog = ref<CapabilityCatalogDto | null>(null)
const selectedId = ref('')
const draft = ref<SkillDefinitionDto>(emptySkill())
const selectedToolKeys = ref<string[]>([])
const artifactsText = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')

function emptySkill(): SkillDefinitionDto {
  return {
    schema_version: 1,
    id: '',
    name: '',
    description: '',
    version: 1,
    scope: 'global',
    entry_prompt: null,
    tool_references: [],
    artifacts: [],
    method_document: '# Skill\n\n1. Describe the method.',
  }
}

const selectedSummary = computed(
  () => catalog.value?.skills.find(item => item.id === selectedId.value) ?? null,
)
const canDelete = computed(() => selectedSummary.value?.scope === 'global')
const unavailableSelectedToolKeys = computed(() => {
  const available = new Set((catalog.value?.effective_tools ?? []).map(item => item.key))
  return selectedToolKeys.value.filter(key => !available.has(key))
})

function applySkill(value: SkillDefinitionDto) {
  draft.value = { ...value }
  selectedId.value = value.id
  selectedToolKeys.value = value.tool_references.map(toolReferenceKey)
  artifactsText.value = value.artifacts.join('\n')
}

async function refreshCatalog(preferredId = selectedId.value) {
  catalog.value = await desktopApi.capabilityCatalog()
  if (preferredId && catalog.value.skills.some(item => item.id === preferredId)) {
    await selectSkill(preferredId)
  } else if (catalog.value.skills[0]) {
    await selectSkill(catalog.value.skills[0].id)
  } else {
    newSkill()
  }
}

async function selectSkill(skillId: string) {
  if (!skillId) return
  busy.value = true
  error.value = ''
  try {
    applySkill(await desktopApi.workbenchSkill(skillId))
    notice.value = ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

function newSkill() {
  selectedId.value = ''
  draft.value = emptySkill()
  selectedToolKeys.value = []
  artifactsText.value = ''
  error.value = ''
  notice.value = '新 Skill 尚未保存。'
}

function toolReferenceKey(reference: ToolReferenceDto) {
  return reference.provider === 'mcp'
    ? `mcp:${reference.connection_id}:${reference.tool_name}`
    : `system:${reference.tool_name}`
}

function toggleTool(toolKey: string, checked: boolean) {
  const next = new Set(selectedToolKeys.value)
  if (checked) next.add(toolKey)
  else next.delete(toolKey)
  selectedToolKeys.value = [...next].sort()
}

function selectedToolReferences(): ToolReferenceDto[] {
  const available = new Map(
    (catalog.value?.effective_tools ?? []).map(tool => [
      tool.key,
      tool.provider === 'mcp'
        ? { provider: 'mcp' as const, connection_id: tool.connection_id, tool_name: tool.tool_name }
        : { provider: 'system' as const, tool_name: tool.tool_name },
    ]),
  )
  const existing = new Map(draft.value.tool_references.map(item => [toolReferenceKey(item), item]))
  return selectedToolKeys.value
    .map(key => available.get(key) ?? existing.get(key))
    .filter((item): item is ToolReferenceDto => Boolean(item))
}

function definition(): SkillDefinitionDto {
  const artifacts = artifactsText.value
    .split('\n')
    .map(item => item.trim())
    .filter(Boolean)
  const references = selectedToolReferences()
  return {
    ...draft.value,
    tool_references: references,
    artifacts,
  }
}

async function validateSkill() {
  busy.value = true
  error.value = ''
  try {
    const result = await desktopApi.validateWorkbenchSkill(definition())
    notice.value = result.ok ? 'Skill 验证通过。' : 'Skill 验证失败。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

async function saveSkill() {
  busy.value = true
  error.value = ''
  try {
    const value = definition()
    const current = catalog.value?.skills.find(item => item.id === value.id)
    const expectedVersion = current?.scope === 'global' ? current.version : 0
    const result = await desktopApi.saveWorkbenchSkill(
      value,
      expectedVersion,
    )
    applySkill(result.skill)
    await refreshCatalog(result.skill.id)
    notice.value = `Skill 已保存为 v${result.skill.version}。`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

async function deleteSkill() {
  if (!selectedId.value || !canDelete.value) return
  if (!window.confirm(`删除 Global Skill “${draft.value.name || selectedId.value}”？`)) return
  busy.value = true
  error.value = ''
  try {
    const deleted = await desktopApi.deleteWorkbenchSkill(selectedId.value)
    if (deleted) {
      const deletedId = selectedId.value
      selectedId.value = ''
      await refreshCatalog('')
      notice.value = `Skill ${deletedId} 已删除。`
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
        <h1 class="m-0 text-xl font-medium tracking-[-0.02em]">Skills</h1>
        <p class="mt-1 mb-0 text-xs leading-[18px] text-muted-foreground">
          管理 AI 的方法论、入口 Prompt、可用 Tool 和预期 Artifact。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <Button variant="outline" size="sm" :disabled="busy" @click="refreshCatalog()">
          <RefreshCw :size="14" />刷新
        </Button>
        <Button variant="outline" size="sm" :disabled="busy" @click="newSkill">
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
          v-for="skill in catalog?.skills ?? []"
          :key="skill.id"
          type="button"
          :class="[
            'mb-1 w-full justify-start rounded-md px-2.5 py-2 text-left transition-colors',
            selectedId === skill.id ? 'bg-secondary' : 'hover:bg-secondary/60',
          ]"
          @click="selectSkill(skill.id)"
        >
          <span class="truncate text-xs font-medium">{{ skill.name }}</span>
        </button>
      </aside>

      <main class="min-h-0 overflow-y-auto p-4">
        <div class="grid max-w-4xl gap-4">
          <div class="grid grid-cols-2 gap-3">
            <label class="field">
              <span>Skill ID</span>
              <input v-model="draft.id" :disabled="Boolean(selectedId)" placeholder="frontend-review" />
            </label>
            <label class="field">
              <span>名称</span>
              <input v-model="draft.name" placeholder="Frontend Review" />
            </label>
          </div>

          <label class="field">
            <span>说明</span>
            <input v-model="draft.description" placeholder="告诉 AI 这个 Skill 什么时候应该使用" />
          </label>

          <label class="field">
            <span>Entry Prompt</span>
            <select v-model="draft.entry_prompt">
              <option :value="null">无入口 Prompt</option>
              <option v-for="prompt in catalog?.prompts ?? []" :key="prompt.id" :value="prompt.id">
                {{ prompt.name }} · {{ prompt.id }}
              </option>
            </select>
          </label>

          <label class="field">
            <span>Method / Instructions</span>
            <textarea v-model="draft.method_document" class="min-h-48 resize-y" spellcheck="false" />
          </label>

          <label class="field">
            <span>Artifacts（每行一个相对路径）</span>
            <textarea v-model="artifactsText" class="min-h-24 resize-y font-mono" spellcheck="false" />
          </label>

          <div class="grid gap-2">
            <div class="text-[11px] font-medium">Allowed Tools</div>
            <div class="grid max-h-64 grid-cols-2 gap-1 overflow-y-auto rounded-md border border-border bg-background p-2 lg:grid-cols-3">
              <label
                v-for="tool in catalog?.effective_tools ?? []"
                :key="tool.key"
                class="flex min-w-0 items-center gap-2 rounded px-2 py-1.5 text-[10px] hover:bg-secondary/60"
              >
                <input
                  type="checkbox"
                  :checked="selectedToolKeys.includes(tool.key)"
                  @change="toggleTool(tool.key, ($event.target as HTMLInputElement).checked)"
                />
                <span class="min-w-0 flex-1 truncate font-mono">
                  {{ tool.provider === 'mcp' ? `${tool.connection_name} / ${tool.tool_name}` : tool.tool_name }}
                </span>
                <span class="flex-none text-[9px] text-muted-foreground">{{ tool.provider === 'mcp' ? 'MCP' : 'System' }}</span>
              </label>
            </div>
            <div v-if="unavailableSelectedToolKeys.length" class="text-[10px] text-amber-600 dark:text-amber-400">
              当前 Skill 还有 {{ unavailableSelectedToolKeys.length }} 个暂不可用 Tool 引用；禁用 MCP 服务不会自动删除 Skill 引用。
            </div>
            <div class="text-[10px] text-muted-foreground">
              System Tool 由程序提供；MCP Tool 来自全局 MCP 服务 Discovery。Skill 只保存稳定 Tool Reference。
            </div>
          </div>

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
                @click="deleteSkill"
              >
                <Trash2 :size="14" />删除
              </Button>
              <Button variant="outline" size="sm" :disabled="busy" @click="validateSkill">
                <CheckCircle2 :size="14" />验证
              </Button>
              <Button size="sm" :disabled="busy" @click="saveSkill">
                <Save :size="14" />保存
              </Button>
            </div>
          </div>
        </div>
      </main>
    </div>
  </section>
</template>

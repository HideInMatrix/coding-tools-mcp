<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { CheckCircle2, Plus, RefreshCw, Save, Trash2 } from '@lucide/vue'
import { desktopApi } from '../api/desktop'
import { Button } from '@/components/ui/button'
import type {
  CapabilityCatalogDto,
  SkillDefinitionDto
} from '../types'

const catalog = ref<CapabilityCatalogDto | null>(null)
const selectedId = ref('')
const draft = ref<SkillDefinitionDto>(emptySkill())
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
    artifacts: [],
    method_document: '# Skill\n\n1. Describe the method.',
  }
}

const selectedSummary = computed(
  () => catalog.value?.skills.find(item => item.id === selectedId.value) ?? null,
)
const canDelete = computed(() => selectedSummary.value?.scope === 'global')

function applySkill(value: SkillDefinitionDto) {
  draft.value = { ...value }
  selectedId.value = value.id
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
  artifactsText.value = ''
  error.value = ''
  notice.value = '新 Skill 尚未保存。'
}

function definition(): SkillDefinitionDto {
  const artifacts = artifactsText.value
    .split('\n')
    .map(item => item.trim())
    .filter(Boolean)
  return {
    ...draft.value,
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
          管理完整的 AI 方法论、执行指令和预期 Artifact。
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
            <span>Method / Instructions</span>
            <textarea v-model="draft.method_document" class="min-h-48 resize-y" spellcheck="false" />
          </label>

          <label class="field">
            <span>Artifacts（每行一个相对路径）</span>
            <textarea v-model="artifactsText" class="min-h-24 resize-y font-mono" spellcheck="false" />
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

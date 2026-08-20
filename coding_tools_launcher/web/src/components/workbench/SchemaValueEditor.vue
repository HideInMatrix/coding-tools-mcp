<script setup lang="ts">
import { computed, ref } from 'vue'

type JsonObject = Record<string, unknown>

const props = defineProps<{
  schema: JsonObject
  modelValue: JsonObject
}>()

const emit = defineEmits<{
  'update:modelValue': [value: JsonObject]
}>()

const errors = ref<Record<string, string>>({})

const required = computed(() => new Set(
  Array.isArray(props.schema.required)
    ? props.schema.required.map(item => String(item))
    : [],
))

const fields = computed(() => {
  const raw = props.schema.properties
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  return Object.entries(raw as Record<string, unknown>)
    .filter(([, value]) => value && typeof value === 'object' && !Array.isArray(value))
    .map(([name, value]) => ({
      name,
      schema: value as JsonObject,
    }))
})

function fieldType(schema: JsonObject): string {
  return typeof schema.type === 'string' ? schema.type : 'string'
}

function update(name: string, value: unknown) {
  emit('update:modelValue', { ...props.modelValue, [name]: value })
}

function updateText(name: string, event: Event) {
  update(name, (event.target as HTMLInputElement).value)
}

function updateNumber(name: string, event: Event, integer: boolean) {
  const raw = (event.target as HTMLInputElement).value
  if (!raw) {
    const next = { ...props.modelValue }
    delete next[name]
    emit('update:modelValue', next)
    return
  }
  const parsed = integer ? Number.parseInt(raw, 10) : Number(raw)
  if (Number.isFinite(parsed)) update(name, parsed)
}

function updateBoolean(name: string, event: Event) {
  update(name, (event.target as HTMLInputElement).checked)
}

function complexText(name: string): string {
  const value = props.modelValue[name]
  if (value === undefined) return ''
  return JSON.stringify(value, null, 2)
}

function updateComplex(name: string, event: Event) {
  const raw = (event.target as HTMLTextAreaElement).value.trim()
  if (!raw) {
    const next = { ...props.modelValue }
    delete next[name]
    emit('update:modelValue', next)
    errors.value = { ...errors.value, [name]: '' }
    return
  }
  try {
    update(name, JSON.parse(raw))
    errors.value = { ...errors.value, [name]: '' }
  } catch (reason) {
    errors.value = {
      ...errors.value,
      [name]: reason instanceof Error ? reason.message : String(reason),
    }
  }
}
</script>

<template>
  <div v-if="fields.length" class="grid gap-3">
    <label v-for="field in fields" :key="field.name" class="field">
      <span class="flex items-center gap-1">
        {{ field.name }}
        <strong v-if="required.has(field.name)" class="font-medium text-destructive">*</strong>
      </span>

      <select
        v-if="Array.isArray(field.schema.enum)"
        :value="String(modelValue[field.name] ?? '')"
        @change="update(field.name, ($event.target as HTMLSelectElement).value)"
      >
        <option value="">请选择</option>
        <option v-for="option in field.schema.enum" :key="String(option)" :value="String(option)">
          {{ option }}
        </option>
      </select>

      <label v-else-if="fieldType(field.schema) === 'boolean'" class="flex h-8 items-center gap-2 rounded-md border border-input px-2.5">
        <input
          type="checkbox"
          :checked="Boolean(modelValue[field.name])"
          @change="updateBoolean(field.name, $event)"
        />
        <span class="text-[11px]">{{ modelValue[field.name] ? 'true' : 'false' }}</span>
      </label>

      <input
        v-else-if="fieldType(field.schema) === 'integer' || fieldType(field.schema) === 'number'"
        type="number"
        :step="fieldType(field.schema) === 'integer' ? '1' : 'any'"
        :value="modelValue[field.name] ?? ''"
        @input="updateNumber(field.name, $event, fieldType(field.schema) === 'integer')"
      />

      <textarea
        v-else-if="fieldType(field.schema) === 'object' || fieldType(field.schema) === 'array'"
        class="min-h-24 resize-y rounded-md border border-input bg-background px-2.5 py-2 font-mono text-[10px] outline-none focus:ring-2 focus:ring-ring/30"
        :value="complexText(field.name)"
        @change="updateComplex(field.name, $event)"
      />

      <input
        v-else
        :value="String(modelValue[field.name] ?? '')"
        @input="updateText(field.name, $event)"
      />

      <small v-if="typeof field.schema.description === 'string' && field.schema.description" class="text-[10px] leading-4 text-muted-foreground">
        {{ field.schema.description }}
      </small>
      <small v-if="errors[field.name]" class="text-[10px] leading-4 text-destructive">
        {{ errors[field.name] }}
      </small>
    </label>
  </div>
  <div v-else class="rounded-md border border-dashed border-border px-3 py-2 text-[10px] leading-4 text-muted-foreground">
    当前 Schema 没有可视化 properties，可使用 Advanced JSON 编辑。
  </div>
</template>

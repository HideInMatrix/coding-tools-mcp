<script setup lang="ts">
import { computed } from 'vue'
import { Plus, Trash2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'

type JsonObject = Record<string, unknown>

const props = defineProps<{
  modelValue: JsonObject
}>()

const emit = defineEmits<{
  'update:modelValue': [value: JsonObject]
}>()

const required = computed(() => new Set(
  Array.isArray(props.modelValue.required)
    ? props.modelValue.required.map(item => String(item))
    : [],
))

const properties = computed(() => {
  const raw = props.modelValue.properties
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  return Object.entries(raw as Record<string, unknown>)
    .filter(([, value]) => value && typeof value === 'object' && !Array.isArray(value))
    .map(([name, value]) => ({ name, schema: value as JsonObject }))
})

function nextSchema(properties: Record<string, unknown>, requiredNames: Set<string>): JsonObject {
  return {
    ...props.modelValue,
    type: 'object',
    properties,
    required: [...requiredNames],
    additionalProperties: props.modelValue.additionalProperties ?? false,
  }
}

function addProperty() {
  const current = {
    ...(props.modelValue.properties && typeof props.modelValue.properties === 'object'
      ? props.modelValue.properties as Record<string, unknown>
      : {}),
  }
  let index = Object.keys(current).length + 1
  let name = `param_${index}`
  while (name in current) {
    index += 1
    name = `param_${index}`
  }
  current[name] = { type: 'string', description: '' }
  emit('update:modelValue', nextSchema(current, new Set(required.value)))
}

function removeProperty(name: string) {
  const current = { ...(props.modelValue.properties as Record<string, unknown> ?? {}) }
  delete current[name]
  const nextRequired = new Set(required.value)
  nextRequired.delete(name)
  emit('update:modelValue', nextSchema(current, nextRequired))
}

function renameProperty(oldName: string, newName: string) {
  const normalized = newName.trim()
  if (!normalized || normalized === oldName) return
  const current = { ...(props.modelValue.properties as Record<string, unknown> ?? {}) }
  if (normalized in current) return
  const value = current[oldName]
  delete current[oldName]
  current[normalized] = value
  const nextRequired = new Set(required.value)
  if (nextRequired.delete(oldName)) nextRequired.add(normalized)
  emit('update:modelValue', nextSchema(current, nextRequired))
}

function updateProperty(name: string, key: string, value: unknown) {
  const current = { ...(props.modelValue.properties as Record<string, unknown> ?? {}) }
  current[name] = {
    ...(current[name] as JsonObject ?? {}),
    [key]: value,
  }
  emit('update:modelValue', nextSchema(current, new Set(required.value)))
}

function setRequired(name: string, checked: boolean) {
  const nextRequired = new Set(required.value)
  if (checked) nextRequired.add(name)
  else nextRequired.delete(name)
  emit('update:modelValue', nextSchema(
    { ...(props.modelValue.properties as Record<string, unknown> ?? {}) },
    nextRequired,
  ))
}

function setAdditionalProperties(checked: boolean) {
  emit('update:modelValue', {
    ...props.modelValue,
    type: 'object',
    additionalProperties: checked,
  })
}
</script>

<template>
  <div class="grid gap-2">
    <div v-for="property in properties" :key="property.name" class="grid gap-2 rounded-md border border-border bg-secondary/20 p-2">
      <div class="grid grid-cols-[minmax(0,1fr)_110px_auto] gap-2">
        <input
          :value="property.name"
          placeholder="参数名"
          @change="renameProperty(property.name, ($event.target as HTMLInputElement).value)"
        />
        <select
          :value="String(property.schema.type ?? 'string')"
          @change="updateProperty(property.name, 'type', ($event.target as HTMLSelectElement).value)"
        >
          <option value="string">String</option>
          <option value="integer">Integer</option>
          <option value="number">Number</option>
          <option value="boolean">Boolean</option>
          <option value="object">Object</option>
          <option value="array">Array</option>
        </select>
        <Button variant="ghost" size="icon" class="h-8 w-8 text-destructive" @click="removeProperty(property.name)">
          <Trash2 :size="13" />
        </Button>
      </div>
      <input
        :value="String(property.schema.description ?? '')"
        placeholder="参数说明"
        @input="updateProperty(property.name, 'description', ($event.target as HTMLInputElement).value)"
      />
      <label class="flex items-center gap-2 text-[10px] text-muted-foreground">
        <input
          type="checkbox"
          :checked="required.has(property.name)"
          @change="setRequired(property.name, ($event.target as HTMLInputElement).checked)"
        />
        Required
      </label>
    </div>

    <div v-if="!properties.length" class="rounded-md border border-dashed border-border px-3 py-2 text-[10px] text-muted-foreground">
      尚未定义输入参数。
    </div>

    <div class="flex items-center justify-between gap-3">
      <Button variant="outline" size="sm" @click="addProperty"><Plus :size="13" />添加参数</Button>
      <label class="flex items-center gap-2 text-[10px] text-muted-foreground">
        <input
          type="checkbox"
          :checked="modelValue.additionalProperties !== false"
          @change="setAdditionalProperties(($event.target as HTMLInputElement).checked)"
        />
        允许额外参数
      </label>
    </div>
  </div>
</template>

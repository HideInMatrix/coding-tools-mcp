<script setup lang="ts">
import type { HTMLAttributes } from 'vue'
import { cn } from '@/lib/utils'

defineOptions({ inheritAttrs: false })

const props = defineProps<{
  modelValue?: string | number
  class?: HTMLAttributes['class']
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function handleInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <input
    v-bind="$attrs"
    data-slot="input-group-control"
    :value="props.modelValue"
    :class="cn(
      'h-8 min-w-0 flex-1 border-0 bg-transparent px-3 text-xs text-foreground outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground',
      props.class,
    )"
    @input="handleInput"
  />
</template>

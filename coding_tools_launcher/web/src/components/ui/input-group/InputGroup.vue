<script setup lang="ts">
import type { HTMLAttributes } from 'vue'
import { cn } from '@/lib/utils'

const props = defineProps<{ class?: HTMLAttributes['class'] }>()
</script>

<template>
  <div
    data-slot="input-group"
    :class="cn(
      'group/input-group relative flex w-full items-center overflow-hidden rounded-md border border-input bg-background shadow-xs transition-[border-color,box-shadow] focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/15',
      props.class,
    )"
  >
    <slot />
  </div>
</template>

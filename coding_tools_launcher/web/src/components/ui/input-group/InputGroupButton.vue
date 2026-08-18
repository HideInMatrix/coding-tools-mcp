<script setup lang="ts">
import type { HTMLAttributes } from 'vue'
import { cn } from '@/lib/utils'

const props = defineProps<{ class?: HTMLAttributes['class']; disabled?: boolean }>()
</script>

<template>
  <button
    type="button"
    data-slot="input-group-button"
    :disabled="props.disabled"
    :class="cn(
      'flex size-8 shrink-0 items-center justify-center border-0 border-l border-border bg-background text-muted-foreground outline-none transition-colors hover:bg-secondary hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/20 disabled:pointer-events-none disabled:opacity-50',
      props.class,
    )"
  >
    <slot />
  </button>
</template>

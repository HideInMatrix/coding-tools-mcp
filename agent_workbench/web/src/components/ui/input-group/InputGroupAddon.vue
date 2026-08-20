<script setup lang="ts">
import type { HTMLAttributes } from 'vue'
import { cn } from '@/lib/utils'

const props = defineProps<{ class?: HTMLAttributes['class'] }>()
</script>

<template>
  <div
    data-slot="input-group-addon"
    :class="cn(
      'flex h-8 shrink-0 items-center border-l border-border bg-muted/60 px-2.5 text-[11px] font-medium text-muted-foreground',
      props.class,
    )"
  >
    <slot />
  </div>
</template>

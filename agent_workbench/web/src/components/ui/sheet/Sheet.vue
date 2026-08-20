<script setup lang="ts">
import { X } from '@lucide/vue'
import { DialogContent, DialogRoot, DialogTitle } from 'reka-ui'

withDefaults(defineProps<{
  open: boolean
  side?: 'left' | 'right'
  title: string
  widthClass?: string
}>(), {
  side: 'right',
  widthClass: 'w-[320px]',
})

const emit = defineEmits<{
  close: []
}>()

function onOpenChange(value: boolean) {
  if (!value) emit('close')
}
</script>

<template>
  <DialogRoot :open="open" :modal="false" @update:open="onOpenChange">
    <DialogContent
      :class="[
        'sheet-panel absolute inset-y-0 z-30 flex max-w-[calc(100%-24px)] flex-col bg-background p-0 shadow-xl outline-none',
        side === 'left'
          ? 'sheet-panel-left left-0 border-r border-border'
          : 'sheet-panel-right right-0 border-l border-border',
        widthClass,
      ]"
      :aria-describedby="undefined"
    >
      <header class="flex h-11 flex-none items-center justify-between gap-3 border-b border-border px-3">
        <DialogTitle class="text-xs font-medium">{{ title }}</DialogTitle>
        <button
          type="button"
          class="h-7 w-7 rounded-md border-0 bg-transparent p-0 text-muted-foreground hover:bg-secondary hover:text-foreground"
          :aria-label="`关闭${title}`"
          @click="emit('close')"
        >
          <X :size="14" />
        </button>
      </header>
      <div class="min-h-0 flex-1 overflow-auto p-3">
        <slot />
      </div>
    </DialogContent>
  </DialogRoot>
</template>

<style scoped>
.sheet-panel-left[data-state='open'] {
  animation: sheet-in-left 160ms ease-out;
}

.sheet-panel-right[data-state='open'] {
  animation: sheet-in-right 160ms ease-out;
}

@keyframes sheet-in-left {
  from {
    opacity: 0;
    transform: translateX(-12px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

@keyframes sheet-in-right {
  from {
    opacity: 0;
    transform: translateX(12px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}
</style>

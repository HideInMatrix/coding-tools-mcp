<script setup lang="ts">
import type { LogEntryDto } from '../types'

defineProps<{ logs: LogEntryDto[] }>()
</script>

<template>
  <section class="m-0 overflow-hidden rounded-lg border border-border bg-popover shadow-sm">
    <div class="flex min-h-11 items-center justify-between border-b border-border px-3.5 text-[11px]">
      <strong class="font-medium">运行日志</strong>
      <span class="text-muted-foreground">{{ logs.length }} 条</span>
    </div>
    <pre class="m-0 min-h-[280px] max-h-[calc(100vh-190px)] overflow-auto bg-[oklch(0.16_0_0)] px-4 py-3.5 font-mono text-[11px] leading-[1.65] text-[oklch(0.86_0_0)]">{{ logs.map(item => item.message).join('\n') || '暂无运行日志' }}</pre>
  </section>
</template>

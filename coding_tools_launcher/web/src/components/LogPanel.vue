<script setup lang="ts">
import type { LogEntryDto } from '../types'

defineProps<{ logs: LogEntryDto[] }>()
</script>

<template>
  <section class="log-panel">
    <div class="log-title"><strong>运行日志</strong><span>{{ logs.length }} 条</span></div>
    <pre>{{ logs.map(item => item.message).join('\n') || '暂无运行日志' }}</pre>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Trash2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { desktopApi } from '../api/desktop'
import type { LogEntryDto } from '../types'
import LogPanel from './LogPanel.vue'

const logs = ref<LogEntryDto[]>([])
const errorMessage = ref('')
const clearing = ref(false)
let cursor = 0
let pollTimer = 0

async function refreshLogs(surfaceError = false) {
  if (clearing.value) return
  try {
    const response = await desktopApi.logs(cursor)
    if (clearing.value) return
    cursor = response.cursor
    logs.value.push(...response.entries)
    if (logs.value.length > 600) logs.value.splice(0, logs.value.length - 600)
    if (surfaceError) errorMessage.value = ''
  } catch (error) {
    if (surfaceError) errorMessage.value = error instanceof Error ? error.message : String(error)
  }
}

async function clearLogs() {
  if (clearing.value) return
  clearing.value = true
  try {
    cursor = await desktopApi.clearLogs()
    logs.value = []
    errorMessage.value = ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    clearing.value = false
  }
}

onMounted(async () => {
  await refreshLogs(true)
  pollTimer = window.setInterval(() => void refreshLogs(false), 900)
})

onBeforeUnmount(() => window.clearInterval(pollTimer))
</script>

<template>
  <section class="grid w-full gap-5">
    <div
      v-if="errorMessage"
      class="flex items-center justify-between gap-3 rounded-[7px] border border-destructive/25 bg-destructive/10 px-3 py-2.5 text-xs text-destructive"
    >
      <span>{{ errorMessage }}</span>
      <button class="border-0 bg-transparent text-lg leading-none text-inherit" @click="errorMessage = ''">×</button>
    </div>

    <header class="flex min-h-8 items-center justify-between gap-4">
      <div>
        <h1 class="m-0 text-xl leading-7 font-medium tracking-[-0.02em]">运行日志</h1>
        <p class="mt-[3px] mb-0 text-xs leading-[18px] text-muted-foreground">查看 MCP Server 与网络提供商的实时运行输出。</p>
      </div>
      <Button
        variant="outline"
        size="sm"
        class="text-destructive hover:bg-destructive/10 hover:text-destructive"
        :disabled="clearing || logs.length === 0"
        @click="clearLogs"
      >
        <Trash2 :size="14" />
        {{ clearing ? '清除中…' : '清除日志' }}
      </Button>
    </header>

    <LogPanel :logs="logs" />
  </section>
</template>

<script setup lang="ts">
import { Info, KeyRound, ScrollText, Server } from '@lucide/vue'
import { useRoute, useRouter } from 'vue-router'
import { Button } from '@/components/ui/button'
import type { AppRouteName } from '../router'

defineProps<{ version: string }>()

const route = useRoute()
const router = useRouter()

function navClass(name: AppRouteName): string[] {
  return [
    'group w-full justify-start gap-2 px-2.5 text-xs font-normal',
    route.name === name
      ? 'bg-secondary text-foreground'
      : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
  ]
}
</script>

<template>
  <aside class="flex w-72 flex-none basis-72 flex-col border-r border-sidebar-border bg-sidebar px-4 py-6 text-sidebar-foreground max-[1050px]:w-[248px] max-[1050px]:basis-[248px]">
    <div class="flex min-h-8 items-start justify-between gap-3 px-2.5">
      <div class="grid min-w-0 gap-0.5">
        <strong class="text-[15px] leading-5 font-semibold tracking-[-0.015em]">Coding Tools MCP</strong>
        <span class="text-[11px] leading-4 font-normal text-muted-foreground">Desktop Manager</span>
      </div>
      <small class="flex-none font-mono text-[11px] leading-4 font-normal text-muted-foreground">v{{ version || '—' }}</small>
    </div>

    <nav class="mt-7 grid gap-1" aria-label="主导航">
      <Button
        variant="ghost"
        size="sm"
        :class="navClass('services')"
        @click="router.push({ name: 'services' })"
      >
        <Server class="flex-none" :size="16" :stroke-width="1.8" />
        <span class="leading-none">服务</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        :class="navClass('oauth')"
        @click="router.push({ name: 'oauth' })"
      >
        <KeyRound class="flex-none" :size="16" :stroke-width="1.8" />
        <span class="leading-none">OAuth 授权</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        :class="navClass('logs')"
        @click="router.push({ name: 'logs' })"
      >
        <ScrollText class="flex-none" :size="16" :stroke-width="1.8" />
        <span class="leading-none">运行日志</span>
      </Button>
    </nav>

    <div class="mt-auto border-t border-sidebar-border pt-4">
      <Button
        variant="ghost"
        size="sm"
        :class="navClass('about')"
        @click="router.push({ name: 'about' })"
      >
        <Info class="flex-none" :size="16" :stroke-width="1.8" />
        <span class="leading-none">关于</span>
      </Button>
    </div>
  </aside>
</template>

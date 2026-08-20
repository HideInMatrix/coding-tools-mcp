<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  Info,
  KeyRound,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  ScrollText,
  Server,
  Sparkles,
  Workflow,
} from '@lucide/vue'
import { useRoute, useRouter } from 'vue-router'
import { Button } from '@/components/ui/button'
import type { AppRouteName } from '../router'

defineProps<{ version: string }>()

const route = useRoute()
const router = useRouter()
const collapsed = ref(false)

onMounted(() => {
  try {
    collapsed.value = window.localStorage.getItem('app-sidebar-collapsed') === '1'
  } catch {
    collapsed.value = false
  }
})

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  try {
    window.localStorage.setItem('app-sidebar-collapsed', collapsed.value ? '1' : '0')
  } catch {
    // WebView storage may be unavailable; the in-memory state still works.
  }
}

function navClass(name: AppRouteName): string[] {
  const active = name === 'workbench'
    ? String(route.name ?? '').startsWith('workbench')
    : route.name === name
  return [
    'group w-full text-xs font-normal',
    collapsed.value ? 'justify-center px-0' : 'justify-start gap-2 px-2.5',
    active
      ? 'bg-secondary text-foreground'
      : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
  ]
}

function subNavClass(name: AppRouteName): string[] {
  return [
    'w-full justify-start gap-2 px-2.5 pl-8 text-[11px] font-normal',
    route.name === name
      ? 'bg-secondary text-foreground'
      : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
  ]
}
</script>

<template>
  <aside
    :class="[
      'flex flex-none flex-col border-r border-sidebar-border bg-sidebar py-5 text-sidebar-foreground transition-[width] duration-200',
      collapsed ? 'w-16 px-2' : 'w-60 px-3',
    ]"
  >
    <div :class="['flex min-h-8 items-start gap-2', collapsed ? 'justify-center' : 'justify-between px-1.5']">
      <div v-if="!collapsed" class="grid min-w-0 gap-0.5">
        <strong class="text-[15px] leading-5 font-semibold tracking-[-0.015em]">MicroMatrix Workbench</strong>
        <span class="text-[11px] leading-4 font-normal text-muted-foreground">Desktop Manager</span>
      </div>
      <div v-if="!collapsed" class="flex items-center gap-1">
        <small class="flex-none font-mono text-[10px] leading-4 font-normal text-muted-foreground">v{{ version || '—' }}</small>
        <Button variant="ghost" size="icon" class="h-7 w-7" title="收起侧边栏" @click="toggleCollapsed">
          <PanelLeftClose :size="15" />
        </Button>
      </div>
      <Button v-else variant="ghost" size="icon" class="h-8 w-8" title="展开侧边栏" @click="toggleCollapsed">
        <PanelLeftOpen :size="16" />
      </Button>
    </div>

    <nav :class="['grid gap-1', collapsed ? 'mt-5' : 'mt-7']" aria-label="主导航">
      <Button
        variant="ghost"
        size="sm"
        :class="navClass('services')"
        :title="collapsed ? '服务' : undefined"
        @click="router.push({ name: 'services' })"
      >
        <Server class="flex-none" :size="16" :stroke-width="1.8" />
        <span v-if="!collapsed" class="leading-none">服务</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        :class="navClass('workbench')"
        :title="collapsed ? 'AI 工作台' : undefined"
        @click="router.push({ name: 'workbench' })"
      >
        <Workflow class="flex-none" :size="16" :stroke-width="1.8" />
        <span v-if="!collapsed" class="leading-none">AI 工作台</span>
      </Button>
      <template v-if="!collapsed">
        <Button
          variant="ghost"
          size="sm"
          :class="subNavClass('workbench-prompts')"
          @click="router.push({ name: 'workbench-prompts' })"
        >
          <MessageSquareText class="flex-none" :size="14" :stroke-width="1.8" />
          <span class="leading-none">Prompts</span>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          :class="subNavClass('workbench-skills')"
          @click="router.push({ name: 'workbench-skills' })"
        >
          <Sparkles class="flex-none" :size="14" :stroke-width="1.8" />
          <span class="leading-none">Skills</span>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          :class="subNavClass('workbench-mcp-connections')"
          @click="router.push({ name: 'workbench-mcp-connections' })"
        >
          <Server class="flex-none" :size="14" :stroke-width="1.8" />
          <span class="leading-none">MCP 服务</span>
        </Button>
      </template>
      <Button
        variant="ghost"
        size="sm"
        :class="navClass('oauth')"
        :title="collapsed ? 'OAuth 授权' : undefined"
        @click="router.push({ name: 'oauth' })"
      >
        <KeyRound class="flex-none" :size="16" :stroke-width="1.8" />
        <span v-if="!collapsed" class="leading-none">OAuth 授权</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        :class="navClass('logs')"
        :title="collapsed ? '运行日志' : undefined"
        @click="router.push({ name: 'logs' })"
      >
        <ScrollText class="flex-none" :size="16" :stroke-width="1.8" />
        <span v-if="!collapsed" class="leading-none">运行日志</span>
      </Button>
    </nav>

    <div class="mt-auto border-t border-sidebar-border pt-4">
      <Button
        variant="ghost"
        size="sm"
        :class="navClass('about')"
        :title="collapsed ? '关于' : undefined"
        @click="router.push({ name: 'about' })"
      >
        <Info class="flex-none" :size="16" :stroke-width="1.8" />
        <span v-if="!collapsed" class="leading-none">关于</span>
      </Button>
    </div>
  </aside>
</template>

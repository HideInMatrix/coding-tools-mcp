<script setup lang="ts">
import { Info, KeyRound, Network, ScrollText, Server } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import type { PageKey } from '../types'

defineProps<{ active: PageKey; version: string }>()
const emit = defineEmits<{ select: [page: PageKey] }>()
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-brand">
      <div>
        <strong>Coding Tools MCP</strong>
        <span>Desktop Manager</span>
      </div>
      <small>v{{ version || '—' }}</small>
    </div>

    <nav class="sidebar-nav" aria-label="主导航">
      <Button variant="ghost" :class="['group nav-item', { active: active === 'servers' }]" @click="emit('select', 'servers')">
        <Server :size="16" :stroke-width="1.8" />
        <span>服务</span>
      </Button>
      <Button variant="ghost" :class="['group nav-item', { active: active === 'gateways' }]" @click="emit('select', 'gateways')">
        <Network :size="16" :stroke-width="1.8" />
        <span>Gateway</span>
      </Button>
      <Button variant="ghost" :class="['group nav-item', { active: active === 'clients' }]" @click="emit('select', 'clients')">
        <KeyRound :size="16" :stroke-width="1.8" />
        <span>OAuth 授权</span>
      </Button>
      <Button variant="ghost" :class="['group nav-item', { active: active === 'logs' }]" @click="emit('select', 'logs')">
        <ScrollText :size="16" :stroke-width="1.8" />
        <span>运行日志</span>
      </Button>
    </nav>

    <div class="sidebar-footer">
      <Button variant="ghost" :class="['group nav-item', { active: active === 'about' }]" @click="emit('select', 'about')">
        <Info :size="16" :stroke-width="1.8" />
        <span>关于</span>
      </Button>
    </div>
  </aside>
</template>

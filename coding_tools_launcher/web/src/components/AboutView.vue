<script setup lang="ts">
import type { ReleaseDto } from '../types'

defineProps<{ version: string; release: ReleaseDto | null; checking: boolean }>()
const emit = defineEmits<{ check: []; open: [url: string] }>()
</script>

<template>
  <section class="content-page about-view">
    <div><h1>关于</h1><p>Coding Tools MCP 版本与更新信息</p></div>
    <div class="about-card">
      <div class="about-logo">CT</div>
      <h2>Coding Tools MCP</h2>
      <div class="about-row"><span>当前版本</span><strong>{{ version }}</strong></div>
      <div class="about-row"><span>GitHub 最新版本</span><strong>{{ release?.latest_version || '未检查' }}</strong></div>
      <p v-if="release?.update_available" class="update-note">发现新版本 {{ release.latest_version }}。</p>
      <p v-else-if="release" class="muted">当前已经是最新版本。</p>
      <button v-if="release?.update_available" class="primary-button full" @click="emit('open', release.download_url || release.release_url)">更新</button>
      <button v-else class="secondary-button full" :disabled="checking" @click="emit('check')">{{ checking ? '正在检查…' : '检查版本' }}</button>
      <small>Copyright © micromatrix.org</small>
    </div>
  </section>
</template>

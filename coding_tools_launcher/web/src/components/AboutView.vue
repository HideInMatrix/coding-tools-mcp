<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ReleaseDto, UpdateStatusDto } from '../types'

const props = defineProps<{
  version: string
  release: ReleaseDto | null
  checking: boolean
  updateStatus: UpdateStatusDto
  updateProxyPrefix: string
  savingProxy: boolean
}>()
const emit = defineEmits<{ check: []; update: []; open: [url: string]; saveProxy: [prefix: string] }>()
const proxyDraft = ref(props.updateProxyPrefix)

watch(() => props.updateProxyPrefix, value => { proxyDraft.value = value })

const updating = computed(() => ['downloading', 'verifying', 'ready', 'installing'].includes(props.updateStatus.state))
const canAutoUpdate = computed(() => Boolean(props.release?.update_download_url && props.release?.checksum_url))

function formatBytes(value: number): string {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size >= 100 || index === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[index]}`
}
</script>

<template>
  <section class="content-page about-view">
    <div><h1>关于</h1><p>Coding Tools MCP 版本与更新信息</p></div>
    <div class="about-card">
      <div class="about-logo">CT</div>
      <h2>Coding Tools MCP</h2>
      <div class="about-row"><span>当前版本</span><strong>{{ version || '—' }}</strong></div>
      <div class="about-row"><span>GitHub 最新版本</span><strong>{{ release?.latest_version || '未检查' }}</strong></div>
      <div class="update-proxy-setting">
        <label for="update-proxy-prefix">GitHub 下载加速前缀</label>
        <div class="update-proxy-control">
          <input
            id="update-proxy-prefix"
            v-model="proxyDraft"
            type="url"
            spellcheck="false"
            placeholder="留空则直连 GitHub"
            @keydown.enter="emit('saveProxy', proxyDraft)"
          />
          <button
            class="secondary-button"
            :disabled="savingProxy || proxyDraft === updateProxyPrefix"
            @click="emit('saveProxy', proxyDraft)"
          >{{ savingProxy ? '保存中…' : '保存' }}</button>
        </div>
        <small>默认使用 https://cdn.gh-proxy.org/；留空可关闭加速。</small>
      </div>
      <p v-if="release?.update_available" class="update-note">发现新版本 {{ release.latest_version }}。</p>
      <p v-else-if="release" class="muted">当前已经是最新版本。</p>

      <div v-if="updating || updateStatus.state === 'error'" class="update-progress-card">
        <div class="update-progress-heading">
          <span>{{ updateStatus.message || '正在处理更新…' }}</span>
          <strong v-if="updateStatus.state === 'downloading'">{{ updateStatus.progress }}%</strong>
        </div>
        <div v-if="updateStatus.state === 'downloading'" class="update-progress-track" aria-label="更新下载进度">
          <div class="update-progress-value" :style="{ width: `${updateStatus.progress}%` }"></div>
        </div>
        <div v-if="updateStatus.state === 'downloading'" class="update-progress-meta">
          <span>{{ formatBytes(updateStatus.downloaded_bytes) }}</span>
          <span v-if="updateStatus.total_bytes">{{ formatBytes(updateStatus.total_bytes) }}</span>
        </div>
      </div>

      <button
        v-if="release?.update_available && canAutoUpdate"
        class="primary-button full"
        :disabled="updating"
        @click="emit('update')"
      >
        {{ updating ? (updateStatus.state === 'installing' ? '正在安装并重启…' : '正在更新…') : (updateStatus.state === 'error' ? '重试更新' : `更新到 ${release.latest_version}`) }}
      </button>
      <button
        v-else-if="release?.update_available"
        class="secondary-button full"
        @click="emit('open', release.download_url || release.release_url)"
      >
        打开下载页面
      </button>
      <button v-else class="secondary-button full" :disabled="checking" @click="emit('check')">{{ checking ? '正在检查…' : '检查版本' }}</button>
      <p v-if="release?.update_available && !canAutoUpdate" class="muted update-fallback-note">当前 Release 缺少自动更新包或 SHA-256 校验文件，请使用手动下载。</p>
      <small>Copyright © micromatrix.org</small>
    </div>
  </section>
</template>

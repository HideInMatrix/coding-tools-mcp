<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Button } from '@/components/ui/button'
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
  <section class="w-full max-w-[700px]">
    <div>
      <h1 class="m-0 text-xl leading-7 font-medium tracking-[-0.02em]">关于</h1>
      <p class="mt-[3px] mb-0 text-xs leading-[18px] text-muted-foreground">Coding Tools MCP 版本与更新信息</p>
    </div>
    <div class="mt-5 max-w-[430px] overflow-hidden rounded-lg border border-border bg-popover p-5 shadow-sm">
      <div class="mb-3.5 grid size-10 place-items-center rounded-lg bg-primary text-xs font-semibold text-primary-foreground">CT</div>
      <h2 class="mt-0 mb-2.5 text-sm font-medium">Coding Tools MCP</h2>
      <div class="flex justify-between gap-4 border-b border-border py-2.5 text-[11px]"><span>当前版本</span><strong>{{ version || '—' }}</strong></div>
      <div class="flex justify-between gap-4 border-b border-border py-2.5 text-[11px]"><span>GitHub 最新版本</span><strong>{{ release?.latest_version || '未检查' }}</strong></div>
      <div class="pt-3 pb-0.5">
        <label class="mb-[7px] block text-[11px] font-medium" for="update-proxy-prefix">GitHub 下载加速前缀</label>
        <div class="flex gap-[7px]">
          <input
            id="update-proxy-prefix"
            v-model="proxyDraft"
            class="h-8 min-w-0 flex-1 rounded-md border border-input bg-background px-[9px] text-[11px] text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/20"
            type="url"
            spellcheck="false"
            placeholder="留空则直连 GitHub"
            @keydown.enter="emit('saveProxy', proxyDraft)"
          />
          <Button
            variant="outline"
            size="sm"
            class="whitespace-nowrap px-[11px]"
            :disabled="savingProxy || proxyDraft === updateProxyPrefix"
            @click="emit('saveProxy', proxyDraft)"
          >{{ savingProxy ? '保存中…' : '保存' }}</Button>
        </div>
        <small class="mt-1.5 block text-[10px] text-muted-foreground">默认使用 https://cdn.gh-proxy.org/；留空可关闭加速。</small>
      </div>
      <p v-if="release?.update_available" class="mt-3 mb-0 rounded-md bg-secondary px-2.5 py-2 text-[11px] text-foreground">发现新版本 {{ release.latest_version }}。</p>
      <p v-else-if="release" class="text-muted-foreground">当前已经是最新版本。</p>

      <div v-if="updating || updateStatus.state === 'error'" class="mt-3 rounded-[7px] border border-border bg-secondary p-2.5">
        <div class="flex items-center justify-between gap-3 text-[11px]">
          <span>{{ updateStatus.message || '正在处理更新…' }}</span>
          <strong v-if="updateStatus.state === 'downloading'" class="font-semibold tabular-nums">{{ updateStatus.progress }}%</strong>
        </div>
        <div v-if="updateStatus.state === 'downloading'" class="mt-[9px] h-[7px] overflow-hidden rounded-full bg-muted-foreground/15" aria-label="更新下载进度">
          <div class="h-full min-w-0 rounded-[inherit] bg-primary transition-[width] duration-200" :style="{ width: `${updateStatus.progress}%` }"></div>
        </div>
        <div v-if="updateStatus.state === 'downloading'" class="mt-1.5 flex items-center justify-between gap-3 text-[10px] tabular-nums text-muted-foreground">
          <span>{{ formatBytes(updateStatus.downloaded_bytes) }}</span>
          <span v-if="updateStatus.total_bytes">{{ formatBytes(updateStatus.total_bytes) }}</span>
        </div>
      </div>

      <Button
        v-if="release?.update_available && canAutoUpdate"
        class="mt-2.5 w-full"
        :disabled="updating"
        @click="emit('update')"
      >
        {{ updating ? (updateStatus.state === 'installing' ? '正在安装并重启…' : '正在更新…') : (updateStatus.state === 'error' ? '重试更新' : `更新到 ${release.latest_version}`) }}
      </Button>
      <Button
        v-else-if="release?.update_available"
        variant="outline"
        class="mt-2.5 w-full"
        @click="emit('open', release.download_url || release.release_url)"
      >
        打开下载页面
      </Button>
      <Button v-else variant="outline" class="mt-2.5 w-full" :disabled="checking" @click="emit('check')">{{ checking ? '正在检查…' : '检查版本' }}</Button>
      <p v-if="release?.update_available && !canAutoUpdate" class="mt-[9px] text-[10px] leading-[15px] text-muted-foreground">当前 Release 缺少自动更新包或 SHA-256 校验文件，请使用手动下载。</p>
      <small class="mt-4 block text-[10px] text-muted-foreground">Copyright © micromatrix.org</small>
    </div>
  </section>
</template>

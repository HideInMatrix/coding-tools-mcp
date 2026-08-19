<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { desktopApi } from '../api/desktop'
import type { ReleaseDto, UpdateStatusDto } from '../types'
import AboutView from './AboutView.vue'

const version = ref('')
const release = ref<ReleaseDto | null>(null)
const checkingUpdate = ref(false)
const updateProxyPrefix = ref('')
const savingUpdateProxy = ref(false)
const errorMessage = ref('')
const updateStatus = ref<UpdateStatusDto>({
  state: 'idle',
  version: '',
  progress: 0,
  downloaded_bytes: 0,
  total_bytes: 0,
  message: '',
})
let installRequested = false
let pollTimer = 0

async function checkUpdate() {
  checkingUpdate.value = true
  errorMessage.value = ''
  try {
    release.value = await desktopApi.checkUpdate()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    checkingUpdate.value = false
  }
}

async function saveUpdateProxy(prefix: string) {
  savingUpdateProxy.value = true
  errorMessage.value = ''
  try {
    updateProxyPrefix.value = await desktopApi.saveUpdateDownloadProxy(prefix)
    release.value = null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    savingUpdateProxy.value = false
  }
}

async function startUpdate() {
  errorMessage.value = ''
  installRequested = false
  try {
    updateStatus.value = await desktopApi.startUpdate()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  }
}

async function refreshUpdateStatus() {
  if (!['downloading', 'verifying', 'ready', 'installing'].includes(updateStatus.value.state)) return
  updateStatus.value = await desktopApi.updateStatus()
  if (updateStatus.value.state === 'ready' && !installRequested) {
    installRequested = true
    try {
      updateStatus.value = await desktopApi.installUpdate()
    } catch (error) {
      installRequested = false
      errorMessage.value = error instanceof Error ? error.message : String(error)
      updateStatus.value = await desktopApi.updateStatus()
    }
  }
}

onMounted(async () => {
  try {
    const [appVersion, proxyPrefix, currentUpdateStatus] = await Promise.all([
      desktopApi.appVersion(),
      desktopApi.updateDownloadProxy(),
      desktopApi.updateStatus(),
    ])
    version.value = appVersion
    updateProxyPrefix.value = proxyPrefix
    updateStatus.value = currentUpdateStatus
    await refreshUpdateStatus()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  }
  pollTimer = window.setInterval(() => void refreshUpdateStatus(), 900)
})

onBeforeUnmount(() => window.clearInterval(pollTimer))
</script>

<template>
  <div class="grid gap-4">
    <div
      v-if="errorMessage"
      class="flex items-center justify-between gap-3 rounded-[7px] border border-destructive/25 bg-destructive/10 px-3 py-2.5 text-xs text-destructive"
    >
      <span>{{ errorMessage }}</span>
      <button class="border-0 bg-transparent text-lg leading-none text-inherit" @click="errorMessage = ''">×</button>
    </div>

    <AboutView
      :version="version"
      :release="release"
      :checking="checkingUpdate"
      :update-status="updateStatus"
      :update-proxy-prefix="updateProxyPrefix"
      :saving-proxy="savingUpdateProxy"
      @check="checkUpdate"
      @update="startUpdate"
      @save-proxy="saveUpdateProxy"
      @open="desktopApi.openExternal"
    />
  </div>
</template>

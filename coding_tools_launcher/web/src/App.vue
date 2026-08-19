<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ChevronDown } from '@lucide/vue'
import { RouterView } from 'vue-router'
import { desktopApi } from './api/desktop'
import AppSidebar from './components/AppSidebar.vue'
import type { PermissionRequestDto } from './types'

const version = ref('')
const errorMessage = ref('')
const permissionRequests = ref<PermissionRequestDto[]>([])
const permissionResponding = ref(false)
const permissionMenuOpen = ref(false)
let pollTimer = 0

const activePermissionRequest = computed(() => permissionRequests.value[0] || null)
const permissionArguments = computed(() => {
  const request = activePermissionRequest.value
  if (!request) return ''
  try {
    return JSON.stringify(request.arguments, null, 2)
  } catch {
    return String(request.arguments)
  }
})

async function refreshPermissionRequests(surfaceError = false) {
  try {
    permissionRequests.value = await desktopApi.listPermissionRequests()
    if (!permissionRequests.value.length) permissionMenuOpen.value = false
    if (surfaceError) errorMessage.value = ''
  } catch (error) {
    if (surfaceError) errorMessage.value = error instanceof Error ? error.message : String(error)
  }
}

function permissionLabel(permission: string) {
  return ({
    network: '访问网络',
    destructive_command: '执行破坏性命令',
    git_metadata_write: '写入 Git 元数据',
    long_timeout: '延长执行时间',
    sensitive_env: '传入敏感环境变量',
    sandbox_env_override: '覆盖沙箱环境变量',
    shell_expansion: '使用 Shell 展开',
    inline_script: '执行内联脚本',
    privileged_executable: '查询并运行用户工具',
    write_generated_or_ignored: '写入生成或忽略文件',
  } as Record<string, string>)[permission] || permission
}

async function respondPermission(decision: 'deny' | 'once' | 'session') {
  const request = activePermissionRequest.value
  if (!request || permissionResponding.value) return

  permissionResponding.value = true
  permissionMenuOpen.value = false
  try {
    const accepted = await desktopApi.respondPermissionRequest(request.request_id, decision)
    if (!accepted) errorMessage.value = '授权请求已过期或不再有效。'
    await refreshPermissionRequests(false)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    permissionResponding.value = false
  }
}

onMounted(async () => {
  const versionRequest = desktopApi.appVersion()
    .then(value => { if (value) version.value = value })
    .catch(() => undefined)

  await refreshPermissionRequests(true)
  await versionRequest
  pollTimer = window.setInterval(() => void refreshPermissionRequests(false), 900)
})

onBeforeUnmount(() => window.clearInterval(pollTimer))
</script>

<template>
  <div class="flex h-screen bg-background">
    <AppSidebar :version="version" />

    <main class="min-w-0 flex-1 overflow-auto">
      <div class="mx-auto min-h-full w-full max-w-[1280px] px-8 py-10 max-[1050px]:px-6 max-[1050px]:py-8">
        <div
          v-if="errorMessage"
          class="sticky top-2 z-30 mb-4 flex items-center justify-between gap-3 rounded-[7px] border border-destructive/25 bg-destructive/10 px-3 py-2.5 text-xs text-destructive"
        >
          <span>{{ errorMessage }}</span>
          <button class="border-0 bg-transparent text-lg leading-none text-inherit" @click="errorMessage = ''">×</button>
        </div>

        <RouterView />
      </div>
    </main>
  </div>

  <div
    v-if="activePermissionRequest"
    class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/40 p-6 backdrop-blur-[2px]"
  >
    <section
      class="max-h-[min(680px,calc(100vh-48px))] w-[min(560px,100%)] overflow-auto rounded-[10px] border border-border bg-background p-[18px] shadow-[0_18px_50px_rgb(0_0_0/0.2)]"
      role="dialog"
      aria-modal="true"
      aria-labelledby="permission-dialog-title"
    >
      <header class="flex items-start justify-between gap-4">
        <div>
          <span class="text-[10px] leading-[14px] font-semibold text-destructive">需要授权</span>
          <h2 id="permission-dialog-title" class="mt-[3px] mb-0 text-[17px] leading-6">{{ permissionLabel(activePermissionRequest.permission) }}</h2>
        </div>
        <span class="max-w-[180px] flex-none overflow-hidden text-ellipsis whitespace-nowrap rounded-full bg-secondary px-[7px] py-[3px] text-[10px] leading-[15px] text-muted-foreground">{{ activePermissionRequest.server_name }}</span>
      </header>

      <p class="mt-3.5 mb-0 text-xs leading-[18px] text-foreground">{{ activePermissionRequest.reason }}</p>

      <dl class="mt-3.5 mb-0 grid grid-cols-2 gap-2">
        <div class="min-w-0 rounded-[7px] border border-border bg-secondary px-2.5 py-2">
          <dt class="text-[9px] leading-[13px] text-muted-foreground">工具</dt>
          <dd class="mt-0.5 mb-0 font-mono text-[11px] leading-4 text-foreground [overflow-wrap:anywhere]">{{ activePermissionRequest.tool_name }}</dd>
        </div>
        <div class="min-w-0 rounded-[7px] border border-border bg-secondary px-2.5 py-2">
          <dt class="text-[9px] leading-[13px] text-muted-foreground">权限</dt>
          <dd class="mt-0.5 mb-0 font-mono text-[11px] leading-4 text-foreground [overflow-wrap:anywhere]">{{ activePermissionRequest.permission }}</dd>
        </div>
      </dl>

      <div class="mt-3.5">
        <span class="text-[10px] leading-[15px] text-muted-foreground">本次调用参数（敏感字段已脱敏）</span>
        <pre class="mt-1.5 mb-0 max-h-[220px] overflow-auto whitespace-pre-wrap rounded-[7px] border border-border bg-secondary p-2.5 text-[10px] leading-4 text-foreground [overflow-wrap:anywhere]">{{ permissionArguments }}</pre>
      </div>

      <p class="mt-3 mb-0 text-[10px] leading-[15px] text-muted-foreground">“仅允许本次”只作用于当前调用；“本次服务会话全部允许”在当前 MCP Server 停止或重启前，对同一已认证客户端自动放行可临时授权的权限。Workspace 边界和不可临时提升的系统限制仍然生效。</p>

      <footer class="mt-4 flex justify-end gap-2">
        <button class="secondary-button min-w-[88px]" :disabled="permissionResponding" @click="respondPermission('deny')">拒绝</button>
        <div class="relative inline-flex">
          <button class="primary-button min-w-[104px] !rounded-r-none !rounded-l-[7px]" :disabled="permissionResponding" @click="respondPermission('once')">仅允许本次</button>
          <button
            class="primary-button w-[34px] min-w-0 !rounded-l-none !rounded-r-[7px] border-l border-l-white/20 px-0"
            :disabled="permissionResponding"
            title="更多授权方式"
            aria-label="更多授权方式"
            @click="permissionMenuOpen = !permissionMenuOpen"
          >
            <ChevronDown :size="16" />
          </button>
          <div v-if="permissionMenuOpen" class="absolute right-0 bottom-[calc(100%+8px)] z-40 w-[250px] rounded-lg border border-border bg-background p-[5px] shadow-[0_14px_36px_rgb(0_0_0/0.18)]">
            <button
              class="flex w-full min-w-0 flex-col items-start gap-0.5 rounded-md border-0 bg-transparent px-2.5 py-[9px] text-left text-foreground hover:bg-secondary"
              type="button"
              @click="respondPermission('session')"
            >
              <strong class="text-[11px] leading-4 font-semibold">本次服务会话全部允许</strong>
              <span class="text-[9px] leading-[14px] text-muted-foreground">直到当前 MCP Server 停止或重启</span>
            </button>
          </div>
        </div>
      </footer>
    </section>
  </div>
</template>

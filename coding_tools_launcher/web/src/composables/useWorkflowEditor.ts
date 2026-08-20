import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { desktopApi } from '../api/desktop'
import type {
  WorkbenchCatalogDto,
  WorkbenchTargetDto,
  WorkflowApprovalDto,
  WorkflowDefinitionDto,
  WorkflowRunDto,
  WorkflowValidationDto,
} from '../types'
import {
  canvasToWorkflow,
  workflowToCanvas,
  type WorkflowCanvasEdge,
  type WorkflowCanvasNode,
} from '../lib/workflowGraph'

const emptyCatalog = (): WorkbenchCatalogDto => ({
  target: {
    target_id: '',
    server_id: '',
    service_name: '',
    profile_name: '',
    workspace: '',
    running: false,
  },
  prompts: [],
  skills: [],
  tools: [],
  effective_tools: [],
  mcp_connections: [],
  workflows: [],
})

export function useWorkflowEditor() {
  const targets = ref<WorkbenchTargetDto[]>([])
  const targetId = ref('')
  const catalog = ref<WorkbenchCatalogDto>(emptyCatalog())
  const workflowId = ref('')
  const workflowName = ref('')
  const workflowDescription = ref('')
  const workflowVersion = ref(1)
  const workflowScope = ref<'built-in' | 'global' | 'workspace'>('workspace')
  const workflowInputsSchema = ref<Record<string, unknown>>({
    type: 'object',
    properties: {},
    additionalProperties: true,
  })
  const workflowTags = ref<string[]>([])
  const workflowMetadata = ref<Record<string, unknown>>({})
  const entryNodeId = ref('')
  const nodes = ref<WorkflowCanvasNode[]>([])
  const edges = ref<WorkflowCanvasEdge[]>([])
  const validation = ref<WorkflowValidationDto | null>(null)
  const runs = ref<WorkflowRunDto[]>([])
  const approvals = ref<WorkflowApprovalDto[]>([])
  const selectedRunId = ref('')
  const busy = ref(false)
  const error = ref('')
  const notice = ref('')
  const history = ref<string[]>([])
  const historyIndex = ref(-1)
  const canUndo = computed(() => historyIndex.value > 0)
  const canRedo = computed(() => historyIndex.value >= 0 && historyIndex.value < history.value.length - 1)
  let pollTimer = 0
  let historyTimer = 0
  let historyApplying = false
  let historyReady = false

  const selectedTarget = computed(
    () => targets.value.find(item => item.target_id === targetId.value) ?? null,
  )
  const selectedRun = computed(
    () => runs.value.find(item => item.run_id === selectedRunId.value) ?? null,
  )
  const workflowApprovals = computed(() => {
    const serverId = selectedTarget.value?.server_id
    return approvals.value.filter(
      item => item.server_id === serverId && (!workflowId.value || runs.value.some(
        run => run.run_id === item.run_id && run.workflow_id === workflowId.value,
      )),
    )
  })

  async function refreshCatalog() {
    if (!targetId.value) {
      catalog.value = emptyCatalog()
      return catalog.value
    }
    catalog.value = await desktopApi.workbenchCatalog(targetId.value)
    return catalog.value
  }

  function definition(): WorkflowDefinitionDto {
    return canvasToWorkflow(
      {
        id: workflowId.value,
        name: workflowName.value,
        description: workflowDescription.value,
        version: workflowVersion.value,
        entryNodeId: entryNodeId.value,
        inputsSchema: workflowInputsSchema.value,
        tags: workflowTags.value,
        metadata: workflowMetadata.value,
      },
      nodes.value,
      edges.value,
    )
  }

  function historySnapshot(): string {
    return JSON.stringify({ ...definition(), scope: workflowScope.value })
  }

  function resetHistory() {
    window.clearTimeout(historyTimer)
    historyTimer = 0
    const snapshot = historySnapshot()
    history.value = [snapshot]
    historyIndex.value = 0
    historyReady = true
  }

  function pushHistory() {
    if (!historyReady || historyApplying) return
    const snapshot = historySnapshot()
    if (history.value[historyIndex.value] === snapshot) return
    history.value = history.value.slice(0, historyIndex.value + 1)
    history.value.push(snapshot)
    if (history.value.length > 100) history.value.shift()
    historyIndex.value = history.value.length - 1
  }

  function scheduleHistory() {
    if (!historyReady || historyApplying) return
    window.clearTimeout(historyTimer)
    historyTimer = window.setTimeout(pushHistory, 140)
  }

  function applyDefinition(value: WorkflowDefinitionDto, reset = true) {
    workflowId.value = value.id
    workflowName.value = value.name
    workflowDescription.value = value.description
    workflowVersion.value = value.version
    workflowScope.value = value.scope ?? 'workspace'
    workflowInputsSchema.value = { ...value.inputs_schema }
    workflowTags.value = [...value.tags]
    workflowMetadata.value = { ...value.metadata }
    entryNodeId.value = value.entry_node_id
    const canvas = workflowToCanvas(value)
    nodes.value = canvas.nodes
    edges.value = canvas.edges
    validation.value = null
    notice.value = ''
    applyRunState()
    if (reset) resetHistory()
  }

  function newWorkflow() {
    const suffix = Date.now().toString(36)
    workflowId.value = `workflow-${suffix}`
    workflowName.value = '新 Workflow'
    workflowDescription.value = ''
    workflowVersion.value = 1
    workflowScope.value = 'workspace'
    workflowInputsSchema.value = {
      type: 'object',
      properties: {},
      additionalProperties: true,
    }
    workflowTags.value = []
    workflowMetadata.value = {}
    entryNodeId.value = ''
    nodes.value = []
    edges.value = []
    validation.value = null
    selectedRunId.value = ''
    notice.value = '新 Workflow 尚未保存。'
    resetHistory()
  }

  async function applyHistorySnapshot(snapshot: string) {
    historyApplying = true
    try {
      applyDefinition(JSON.parse(snapshot) as WorkflowDefinitionDto, false)
      await nextTick()
    } finally {
      historyApplying = false
    }
  }

  async function undo() {
    if (!canUndo.value) return
    window.clearTimeout(historyTimer)
    historyIndex.value -= 1
    await applyHistorySnapshot(history.value[historyIndex.value])
    notice.value = '已撤销上一步 Workflow 编辑。'
  }

  async function redo() {
    if (!canRedo.value) return
    window.clearTimeout(historyTimer)
    historyIndex.value += 1
    await applyHistorySnapshot(history.value[historyIndex.value])
    notice.value = '已恢复下一步 Workflow 编辑。'
  }

  async function refreshTargets() {
    targets.value = await desktopApi.listWorkbenchTargets()
    if (!targetId.value || !targets.value.some(item => item.target_id === targetId.value)) {
      targetId.value = targets.value[0]?.target_id ?? ''
    }
    if (targetId.value) await loadTarget(targetId.value)
  }

  async function loadTarget(nextTargetId: string) {
    targetId.value = nextTargetId
    error.value = ''
    if (!nextTargetId) {
      catalog.value = emptyCatalog()
      newWorkflow()
      return
    }
    busy.value = true
    try {
      await refreshCatalog()
      await refreshRuntimeState()
      const preferred = catalog.value.workflows.find(item => item.id === workflowId.value)
        ?? catalog.value.workflows[0]
      if (preferred) await loadWorkflow(preferred.id)
      else newWorkflow()
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
    } finally {
      busy.value = false
    }
  }

  async function loadWorkflow(nextWorkflowId: string) {
    if (!targetId.value || !nextWorkflowId) return
    busy.value = true
    error.value = ''
    try {
      const value = await desktopApi.workbenchWorkflow(targetId.value, nextWorkflowId)
      applyDefinition(value)
      const latest = runs.value.find(run => run.workflow_id === nextWorkflowId)
      selectedRunId.value = latest?.run_id ?? ''
      applyRunState()
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
    } finally {
      busy.value = false
    }
  }

  async function validate() {
    if (!targetId.value) return null
    busy.value = true
    error.value = ''
    try {
      validation.value = await desktopApi.validateWorkbenchWorkflow(
        targetId.value,
        definition(),
      )
      notice.value = validation.value.ok
        ? '后端 Validator 验证通过。'
        : `验证失败：${validation.value.errors.length} 个错误。`
      return validation.value
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
      return null
    } finally {
      busy.value = false
    }
  }

  async function save() {
    if (!targetId.value) return null
    busy.value = true
    error.value = ''
    try {
      const current = catalog.value.workflows.find(item => item.id === workflowId.value)
      const expectedVersion = current?.scope === 'workspace' ? current.version : 0
      const result = await desktopApi.saveWorkbenchWorkflow(
        targetId.value,
        definition(),
        expectedVersion,
      )
      validation.value = result
      if (!result.ok || !result.saved) {
        notice.value = `保存失败：${result.errors.length} 个验证错误。`
        return result
      }
      applyDefinition(result.workflow, false)
      await refreshCatalog()
      notice.value = `Workflow 已保存为 v${result.workflow.version}。`
      return result
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
      return null
    } finally {
      busy.value = false
    }
  }

  async function remove() {
    if (!targetId.value || workflowScope.value !== 'workspace') return false
    busy.value = true
    try {
      const deleted = await desktopApi.deleteWorkbenchWorkflow(targetId.value, workflowId.value)
      await refreshCatalog()
      if (deleted) {
        const next = catalog.value.workflows[0]
        if (next) await loadWorkflow(next.id)
        else newWorkflow()
        notice.value = 'Workflow 已删除，列表已刷新。'
      }
      return deleted
    } finally {
      busy.value = false
    }
  }

  function applyRunState() {
    const run = selectedRun.value
    for (const node of nodes.value) {
      let status = 'idle'
      if (run) {
        status = run.node_states[node.id]?.status ?? (
          run.engine_state.ready.includes(node.id) ? 'ready' : 'idle'
        )
      }
      node.data.status = status
    }
  }

  async function refreshRuntimeState() {
    if (!targetId.value) return
    const [nextRuns, nextApprovals] = await Promise.all([
      desktopApi.listWorkbenchRuns(targetId.value),
      desktopApi.listWorkflowApprovals(),
    ])
    runs.value = nextRuns
    approvals.value = nextApprovals
    if (
      selectedRunId.value
      && !runs.value.some(item => item.run_id === selectedRunId.value)
    ) {
      selectedRunId.value = ''
    }
    if (!selectedRunId.value && workflowId.value) {
      selectedRunId.value = runs.value.find(
        item => item.workflow_id === workflowId.value,
      )?.run_id ?? ''
    }
    applyRunState()
  }

  async function respondApproval(requestId: string, approved: boolean) {
    const ok = await desktopApi.respondWorkflowApproval(requestId, approved)
    notice.value = ok
      ? '审批决策已签名发送，等待 AI 调用 workflow_continue 继续 Run。'
      : '审批响应失败或请求已失效。'
    await refreshRuntimeState()
    return ok
  }

  function startPolling() {
    window.clearInterval(pollTimer)
    pollTimer = window.setInterval(() => void refreshRuntimeState(), 1500)
  }

  function stopPolling() {
    window.clearInterval(pollTimer)
    pollTimer = 0
  }

  watch(
    () => historySnapshot(),
    () => scheduleHistory(),
    { deep: true },
  )

  onBeforeUnmount(() => {
    stopPolling()
    window.clearTimeout(historyTimer)
  })

  return {
    targets,
    targetId,
    selectedTarget,
    catalog,
    workflowId,
    workflowName,
    workflowDescription,
    workflowVersion,
    workflowScope,
    workflowInputsSchema,
    workflowTags,
    workflowMetadata,
    entryNodeId,
    nodes,
    edges,
    validation,
    runs,
    selectedRunId,
    selectedRun,
    workflowApprovals,
    busy,
    error,
    notice,
    canUndo,
    canRedo,
    definition,
    newWorkflow,
    refreshTargets,
    loadTarget,
    loadWorkflow,
    validate,
    save,
    remove,
    refreshRuntimeState,
    respondApproval,
    undo,
    redo,
    startPolling,
    stopPolling,
  }
}

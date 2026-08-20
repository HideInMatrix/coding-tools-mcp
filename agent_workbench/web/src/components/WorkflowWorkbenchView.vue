<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Braces,
  Check,
  CheckCircle2,
  CircleStop,
  Copy,
  FileOutput,
  GitBranch,
  MessageSquareText,
  PanelLeftOpen,
  PanelRightOpen,
  Plus,
  Redo2,
  Save,
  Scan,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  Undo2,
  Wrench,
  X,
} from '@lucide/vue'
import {
  Handle,
  Position,
  VueFlow,
  useVueFlow,
  type Connection,
} from '@vue-flow/core'
import { Button } from '@/components/ui/button'
import { Sheet } from '@/components/ui/sheet'
import ObjectSchemaBuilder from './workbench/ObjectSchemaBuilder.vue'
import SchemaValueEditor from './workbench/SchemaValueEditor.vue'
import { useWorkflowEditor } from '../composables/useWorkflowEditor'
import type { WorkflowCanvasEdge, WorkflowCanvasNode } from '../lib/workflowGraph'
import type { EffectiveToolDto, WorkflowEdgeDto, WorkflowNodeKind } from '../types'

const editor = useWorkflowEditor()
const selectedNodeId = ref('')
const selectedEdgeId = ref('')
const configError = ref('')
const workflowSchemaError = ref('')
const nodeRoleError = ref('')
const nodeLibraryOpen = ref(false)
const inspectorOpen = ref(false)
const nodeLibraryQuery = ref('')
const capabilityQuery = ref('')
type WorkflowNodeRole = 'input' | 'process' | 'output'
const nodeRoles: WorkflowNodeRole[] = ['input', 'process', 'output']
const newNodeRole = ref<WorkflowNodeRole>('process')
const canvasHost = ref<HTMLElement | null>(null)
const { fitView, onPaneReady, setViewport } = useVueFlow()

let paneReady = false
let resizeObserver: ResizeObserver | null = null
let fitTimer = 0
let fitRaf = 0
let fitRafAfterLayout = 0
let lastCanvasWidth = 0
let lastCanvasHeight = 0

function cancelScheduledFit() {
  window.clearTimeout(fitTimer)
  window.cancelAnimationFrame(fitRaf)
  window.cancelAnimationFrame(fitRafAfterLayout)
  fitTimer = 0
  fitRaf = 0
  fitRafAfterLayout = 0
}

function scheduleFitView(duration = 0, delay = 60) {
  if (!paneReady || !editor.nodes.value.length) return
  cancelScheduledFit()
  fitTimer = window.setTimeout(async () => {
    await nextTick()
    // Custom nodes need one layout frame to publish their measured dimensions
    // before fitView can calculate a reliable viewport.
    fitRaf = window.requestAnimationFrame(() => {
      fitRafAfterLayout = window.requestAnimationFrame(() => {
        void fitView({ padding: 0.18, duration })
      })
    })
  }, delay)
}

function fitCanvas() {
  if (!editor.nodes.value.length) {
    void setViewport({ x: 0, y: 0, zoom: 1 })
    return
  }
  scheduleFitView(180, 0)
}

async function changeTarget(event: Event) {
  await editor.loadTarget((event.target as HTMLSelectElement).value)
  scheduleFitView(0, 80)
}

async function changeWorkflow(event: Event) {
  await editor.loadWorkflow((event.target as HTMLSelectElement).value)
  scheduleFitView(0, 80)
}

onPaneReady(() => {
  paneReady = true
  scheduleFitView(0, 0)
})

const nodeDefinitions: Record<WorkflowNodeKind, {
  label: string
  description: string
}> = {
  prompt: { label: 'Prompt', description: '高级：直接渲染 Prompt 并等待 AI 返回结果' },
  skill: { label: 'Skill', description: '调用用户定义的 AI 方法论与能力包' },
  tool: { label: 'Tool', description: '执行 System Tool 或外部 MCP Tool' },
  approval: { label: 'Approval', description: '等待 Desktop 用户签名批准' },
  condition: { label: 'Condition', description: '使用受限表达式选择分支' },
  artifact: { label: 'Artifact', description: '保存已有节点输出到 Run Artifact' },
}

const nodeGroups: Array<{
  label: string
  description: string
  kinds: WorkflowNodeKind[]
}> = [
  { label: 'AI', description: '让宿主 AI 按可复用能力工作。', kinds: ['skill'] },
  { label: 'Actions', description: '执行确定性的本地或外部能力。', kinds: ['tool'] },
  {
    label: 'Flow Control',
    description: '控制分支、人工边界与结果沉淀。',
    kinds: ['condition', 'approval', 'artifact'],
  },
  {
    label: 'Advanced',
    description: '底层模型指令节点，普通流程优先使用 Skill。',
    kinds: ['prompt'],
  },
]

const filteredNodeGroups = computed(() => {
  const query = nodeLibraryQuery.value.trim().toLowerCase()
  if (!query) return nodeGroups
  return nodeGroups
    .map(group => ({
      ...group,
      kinds: group.kinds.filter(kind => {
        const definition = nodeDefinitions[kind]
        return [kind, definition.label, definition.description, group.label]
          .some(value => value.toLowerCase().includes(query))
      }),
    }))
    .filter(group => group.kinds.length > 0)
})

const edgeConditions: WorkflowEdgeDto['condition'][] = [
  'success',
  'failure',
  'approved',
  'rejected',
  'true',
  'false',
]

const selectedNode = computed(
  () => editor.nodes.value.find(node => node.id === selectedNodeId.value) ?? null,
)
const selectedEdge = computed(
  () => editor.edges.value.find(edge => edge.id === selectedEdgeId.value) ?? null,
)
const canDeleteWorkflow = computed(() => (
  editor.workflowScope.value === 'workspace'
  && editor.catalog.value.workflows.some(
    item => item.id === editor.workflowId.value && item.scope === 'workspace',
  )
))
const selectedPrompt = computed(() => {
  if (!selectedNode.value || selectedNode.value.data.kind !== 'prompt') return null
  const id = String(selectedNode.value.data.config.prompt_id ?? '')
  return editor.catalog.value.prompts.find(item => item.id === id) ?? null
})
const filteredPrompts = computed(() => {
  const query = capabilityQuery.value.trim().toLowerCase()
  if (!query) return editor.catalog.value.prompts
  const selectedId = String(selectedNode.value?.data.config.prompt_id ?? '')
  return editor.catalog.value.prompts.filter(item => (
    item.id === selectedId
    || [item.id, item.name, item.description].some(value => value.toLowerCase().includes(query))
  ))
})
const selectedSkill = computed(() => {
  if (!selectedNode.value || selectedNode.value.data.kind !== 'skill') return null
  const id = String(selectedNode.value.data.config.skill_id ?? '')
  return editor.catalog.value.skills.find(item => item.id === id) ?? null
})
const filteredSkills = computed(() => {
  const query = capabilityQuery.value.trim().toLowerCase()
  if (!query) return editor.catalog.value.skills
  const selectedId = String(selectedNode.value?.data.config.skill_id ?? '')
  return editor.catalog.value.skills.filter(item => (
    item.id === selectedId
    || [item.id, item.name, item.description].some(value => value.toLowerCase().includes(query))
  ))
})
const selectedToolProvider = computed<'system' | 'mcp'>(() => {
  if (!selectedNode.value || selectedNode.value.data.kind !== 'tool') return 'system'
  return selectedNode.value.data.config.provider === 'mcp' ? 'mcp' : 'system'
})
const selectedTool = computed<EffectiveToolDto | null>(() => {
  if (!selectedNode.value || selectedNode.value.data.kind !== 'tool') return null
  const provider = selectedToolProvider.value
  const toolName = String(selectedNode.value.data.config.tool_name ?? '')
  const connectionId = String(selectedNode.value.data.config.connection_id ?? '')
  return editor.catalog.value.effective_tools.find(item => (
    item.provider === provider
    && item.tool_name === toolName
    && (provider === 'system' || item.connection_id === connectionId)
  )) ?? null
})
const filteredSystemTools = computed(() => {
  const query = capabilityQuery.value.trim().toLowerCase()
  const selectedName = String(selectedNode.value?.data.config.tool_name ?? '')
  return editor.catalog.value.effective_tools.filter(item => (
    item.provider === 'system'
    && (
      item.tool_name === selectedName
      || !query
      || [item.tool_name, item.description, item.key].some(value => value.toLowerCase().includes(query))
    )
  ))
})
const selectedArgumentSchema = computed<Record<string, unknown>>(() => {
  if (!selectedNode.value) return { type: 'object', properties: {} }
  if (selectedNode.value.data.kind === 'prompt') {
    return promptArgumentsSchema(selectedPrompt.value?.arguments ?? [])
  }
  if (selectedNode.value.data.kind === 'skill') {
    const promptId = selectedSkill.value?.entry_prompt
    const prompt = editor.catalog.value.prompts.find(item => item.id === promptId)
    return promptArgumentsSchema(prompt?.arguments ?? [])
  }
  if (selectedNode.value.data.kind === 'tool') {
    return selectedTool.value?.input_schema ?? { type: 'object', properties: {} }
  }
  return { type: 'object', properties: {} }
})
const selectedArguments = computed<Record<string, unknown>>(() => {
  const value = selectedNode.value?.data.config.arguments
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
})
const enabledMcpConnections = computed(() => (
  editor.catalog.value.mcp_connections.filter(item => item.enabled)
))
const selectedNodeRole = computed<WorkflowNodeRole>(() => {
  if (!selectedNode.value) return 'process'
  return nodeRole(selectedNode.value.id, selectedNode.value.data.config)
})

function promptArgumentsSchema(argumentsList: Array<{ name: string; description?: string; required?: boolean }>): Record<string, unknown> {
  return {
    type: 'object',
    properties: Object.fromEntries(argumentsList.map(argument => [
      argument.name,
      { type: 'string', description: argument.description ?? '' },
    ])),
    required: argumentsList.filter(argument => argument.required).map(argument => argument.name),
    additionalProperties: false,
  }
}

function validationIssues(subject: string) {
  return [
    ...(editor.validation.value?.errors ?? []),
    ...(editor.validation.value?.warnings ?? []),
  ].filter(item => item.subject === subject)
}

function nodeValidationClass(nodeId: string): string {
  return editor.validation.value?.errors.some(item => item.subject === nodeId)
    ? 'ring-2 ring-destructive/60 ring-offset-1 ring-offset-background'
    : ''
}

function nodeRole(nodeId: string, config: Record<string, unknown>): WorkflowNodeRole {
  if (nodeId === editor.entryNodeId.value) return 'input'
  return config.node_role === 'output' ? 'output' : 'process'
}

function nodeRoleLabel(role: WorkflowNodeRole): string {
  return role === 'input' ? '输入' : role === 'output' ? '输出' : '处理'
}

function withoutNodeRole(config: Record<string, unknown>): Record<string, unknown> {
  const next = { ...config }
  delete next.node_role
  return next
}

function setEntryNode(nodeId: string) {
  const node = editor.nodes.value.find(item => item.id === nodeId)
  if (!node) return
  if (editor.edges.value.some(edge => edge.target === nodeId)) {
    nodeRoleError.value = '输入节点不能存在入边。请先删除指向该节点的连线。'
    return
  }
  editor.entryNodeId.value = nodeId
  if (node.data.config.node_role === 'output') {
    node.data.config = withoutNodeRole(node.data.config)
  }
  nodeRoleError.value = ''
}

function setSelectedNodeRole(role: WorkflowNodeRole) {
  if (!selectedNode.value) return
  const node = selectedNode.value
  if (role === 'input') {
    setEntryNode(node.id)
    return
  }
  if (node.id === editor.entryNodeId.value) {
    nodeRoleError.value = '当前节点是 Workflow 入口。请先将其他节点设为输入节点，再修改它的角色。'
    return
  }
  if (role === 'output') {
    if (editor.edges.value.some(edge => edge.source === node.id)) {
      nodeRoleError.value = '输出节点不能存在出边。请先删除从该节点出发的连线。'
      return
    }
    node.data.config = { ...node.data.config, node_role: 'output' }
  } else {
    node.data.config = withoutNodeRole(node.data.config)
  }
  nodeRoleError.value = ''
}

function changeSelectedNodeRole(event: Event) {
  setSelectedNodeRole((event.target as HTMLSelectElement).value as WorkflowNodeRole)
}

function defaultConfig(kind: WorkflowNodeKind): Record<string, unknown> {
  if (kind === 'prompt') {
    return { prompt_id: editor.catalog.value.prompts[0]?.id ?? '', arguments: {} }
  }
  if (kind === 'skill') {
    return { skill_id: editor.catalog.value.skills[0]?.id ?? '', arguments: {} }
  }
  if (kind === 'tool') {
    const first = editor.catalog.value.effective_tools.find(item => item.provider === 'system')
      ?? editor.catalog.value.effective_tools[0]
    if (!first) return { provider: 'system', tool_name: '', arguments: {} }
    return {
      provider: first.provider,
      ...(first.provider === 'mcp' ? { connection_id: first.connection_id ?? '' } : {}),
      tool_name: first.tool_name,
      arguments: {},
    }
  }
  if (kind === 'approval') return { title: '确认后继续', description: '' }
  if (kind === 'condition') return { expression: 'true' }
  if (kind === 'artifact') {
    return {
      artifact_id: `artifact-${Date.now().toString(36)}`,
      source_node_id: editor.nodes.value[0]?.id ?? '',
      format: 'json',
    }
  }
  return {}
}

function addNode(kind: WorkflowNodeKind) {
  const index = editor.nodes.value.length
  const item = nodeDefinitions[kind]
  const id = `${kind}-${Date.now().toString(36)}`
  const requestedRole: WorkflowNodeRole = editor.nodes.value.length === 0 ? 'input' : newNodeRole.value
  const config = defaultConfig(kind)
  if (requestedRole === 'output') config.node_role = 'output'
  editor.nodes.value.push({
    id,
    type: 'workflow',
    position: {
      x: 80 + (index % 3) * 260,
      y: 100 + Math.floor(index / 3) * 160,
    },
    data: {
      label: item?.label ?? kind,
      kind,
      config,
      policy: {
        approval: kind === 'approval' ? 'required' : 'none',
        on_error: 'stop',
      },
      status: 'idle',
    },
  })
  if (requestedRole === 'input' || !editor.entryNodeId.value) editor.entryNodeId.value = id
  selectedNodeId.value = id
  selectedEdgeId.value = ''
  nodeLibraryOpen.value = false
  inspectorOpen.value = true
  newNodeRole.value = 'process'
  scheduleFitView(160, 80)
}

function duplicateSelectedNode() {
  if (!selectedNode.value) return
  const original = selectedNode.value
  const id = `${original.data.kind}-${Date.now().toString(36)}`
  const config = withoutNodeRole(
    JSON.parse(JSON.stringify(original.data.config)) as Record<string, unknown>,
  )
  editor.nodes.value.push({
    id,
    type: 'workflow',
    position: { x: original.position.x + 36, y: original.position.y + 36 },
    data: {
      label: `${original.data.label} Copy`,
      kind: original.data.kind,
      config,
      policy: { ...original.data.policy },
      status: 'idle',
    },
  })
  selectedNodeId.value = id
  selectedEdgeId.value = ''
  inspectorOpen.value = true
}

function mcpTools(connectionId: string): EffectiveToolDto[] {
  return editor.catalog.value.effective_tools.filter(item => (
    item.provider === 'mcp' && item.connection_id === connectionId
  ))
}

function filteredMcpTools(connectionId: string): EffectiveToolDto[] {
  const query = capabilityQuery.value.trim().toLowerCase()
  const selectedName = String(selectedNode.value?.data.config.tool_name ?? '')
  return mcpTools(connectionId).filter(item => (
    item.tool_name === selectedName
    || !query
    || [item.tool_name, item.description, item.key].some(value => value.toLowerCase().includes(query))
  ))
}

function setToolReference(
  provider: 'system' | 'mcp',
  connectionId: string | undefined,
  toolName: string,
) {
  if (!selectedNode.value) return
  const next: Record<string, unknown> = {
    ...selectedNode.value.data.config,
    provider,
    tool_name: toolName,
  }
  if (provider === 'mcp') next.connection_id = connectionId ?? ''
  else delete next.connection_id
  selectedNode.value.data.config = next
}

function changeToolProvider(event: Event) {
  const provider = (event.target as HTMLSelectElement).value as 'system' | 'mcp'
  if (provider === 'system') {
    const first = editor.catalog.value.effective_tools.find(item => item.provider === 'system')
    setToolReference('system', undefined, first?.tool_name ?? '')
    return
  }
  const connection = enabledMcpConnections.value.find(item => mcpTools(item.id).length > 0)
    ?? enabledMcpConnections.value[0]
  const first = connection ? mcpTools(connection.id)[0] : undefined
  setToolReference('mcp', connection?.id, first?.tool_name ?? '')
}

function changeMcpConnection(event: Event) {
  const connectionId = (event.target as HTMLSelectElement).value
  setToolReference('mcp', connectionId, mcpTools(connectionId)[0]?.tool_name ?? '')
}

function defaultEdgeCondition(sourceId: string): WorkflowEdgeDto['condition'] {
  const source = editor.nodes.value.find(node => node.id === sourceId)
  if (source?.data.kind === 'approval') return 'approved'
  if (source?.data.kind === 'condition') return 'true'
  return 'success'
}

function onConnect(connection: Connection) {
  if (!connection.source || !connection.target || connection.source === connection.target) return
  const sourceNode = editor.nodes.value.find(node => node.id === connection.source)
  const targetNode = editor.nodes.value.find(node => node.id === connection.target)
  if (!sourceNode || !targetNode) return
  if (nodeRole(sourceNode.id, sourceNode.data.config) === 'output') return
  if (nodeRole(targetNode.id, targetNode.data.config) === 'input') return
  const duplicate = editor.edges.value.some(
    edge => edge.source === connection.source && edge.target === connection.target,
  )
  if (duplicate) return
  const condition = defaultEdgeCondition(connection.source)
  const id = `${connection.source}-${connection.target}-${Date.now().toString(36)}`
  editor.edges.value.push({
    id,
    source: connection.source,
    target: connection.target,
    label: condition,
    data: { condition },
  })
  selectedEdgeId.value = id
  selectedNodeId.value = ''
}

function selectNode(node: WorkflowCanvasNode) {
  selectedNodeId.value = node.id
  selectedEdgeId.value = ''
  capabilityQuery.value = ''
  inspectorOpen.value = true
}

function selectEdge(edge: WorkflowCanvasEdge) {
  selectedEdgeId.value = edge.id
  selectedNodeId.value = ''
  inspectorOpen.value = true
}

function clearSelection() {
  selectedNodeId.value = ''
  selectedEdgeId.value = ''
}

function removeSelected() {
  const label = selectedNode.value?.data.label ?? selectedEdge.value?.id ?? '当前选择'
  if (!window.confirm(`确认删除「${label}」？`)) return
  if (selectedNode.value) {
    const id = selectedNode.value.id
    editor.nodes.value = editor.nodes.value.filter(node => node.id !== id)
    editor.edges.value = editor.edges.value.filter(
      edge => edge.source !== id && edge.target !== id,
    )
    if (editor.entryNodeId.value === id) {
      const nextEntry = editor.nodes.value.find(
        node => !editor.edges.value.some(edge => edge.target === node.id),
      ) ?? editor.nodes.value[0]
      if (nextEntry) setEntryNode(nextEntry.id)
      else editor.entryNodeId.value = ''
    }
    selectedNodeId.value = ''
    return
  }
  if (selectedEdge.value) {
    editor.edges.value = editor.edges.value.filter(edge => edge.id !== selectedEdge.value?.id)
    selectedEdgeId.value = ''
  }
}

async function removeWorkflow() {
  if (!canDeleteWorkflow.value) return
  if (!window.confirm(`确认删除 Workflow「${editor.workflowName.value}」？`)) return
  await editor.remove()
}

function setNodeConfig(key: string, value: unknown) {
  if (!selectedNode.value) return
  selectedNode.value.data.config = {
    ...selectedNode.value.data.config,
    [key]: value,
  }
}

function setArguments(event: Event) {
  if (!selectedNode.value) return
  const raw = (event.target as HTMLTextAreaElement).value.trim()
  try {
    const parsed = raw ? JSON.parse(raw) : {}
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('Arguments 必须是 JSON object')
    }
    setNodeConfig('arguments', parsed)
    configError.value = ''
  } catch (reason) {
    configError.value = reason instanceof Error ? reason.message : String(reason)
  }
}

function setVisualArguments(value: Record<string, unknown>) {
  setNodeConfig('arguments', value)
  configError.value = ''
}

function argumentsText() {
  const value = selectedNode.value?.data.config.arguments
  return JSON.stringify(value && typeof value === 'object' ? value : {}, null, 2)
}

function workflowInputsSchemaText() {
  return JSON.stringify(editor.workflowInputsSchema.value, null, 2)
}

function setWorkflowInputsSchema(event: Event) {
  const raw = (event.target as HTMLTextAreaElement).value.trim()
  try {
    const parsed = raw ? JSON.parse(raw) : { type: 'object', additionalProperties: true }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('Inputs Schema 必须是 JSON object')
    }
    editor.workflowInputsSchema.value = parsed as Record<string, unknown>
    workflowSchemaError.value = ''
  } catch (reason) {
    workflowSchemaError.value = reason instanceof Error ? reason.message : String(reason)
  }
}

function setWorkflowTags(event: Event) {
  editor.workflowTags.value = (event.target as HTMLInputElement).value
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
}

function setEdgeCondition(value: WorkflowEdgeDto['condition']) {
  if (!selectedEdge.value) return
  selectedEdge.value.data = { condition: value }
  selectedEdge.value.label = value
}

function nodeIcon(kind: WorkflowNodeKind) {
  return {
    prompt: MessageSquareText,
    skill: Sparkles,
    tool: Wrench,
    approval: ShieldCheck,
    condition: GitBranch,
    artifact: FileOutput,
  }[kind]
}

function nodeKindBorderClass(kind: WorkflowNodeKind) {
  return {
    prompt: 'border-sky-500/80 dark:border-sky-400/80',
    skill: 'border-violet-500/80 dark:border-violet-400/80',
    tool: 'border-blue-500/80 dark:border-blue-400/80',
    approval: 'border-amber-500/80 dark:border-amber-400/80',
    condition: 'border-orange-500/80 dark:border-orange-400/80',
    artifact: 'border-emerald-500/80 dark:border-emerald-400/80',
  }[kind]
}

function nodeStateClass(status: string, selected: boolean) {
  const selectedClass = selected ? 'ring-2 ring-primary/25 ring-offset-1 ring-offset-background' : ''
  if (status === 'succeeded' || status === 'approved') return `bg-green-500/10 ${selectedClass}`
  if (status === 'failed' || status === 'rejected') return `bg-destructive/10 ${selectedClass}`
  if (status === 'waiting_model' || status === 'waiting_approval' || status === 'ready') {
    return `bg-yellow-400/10 ${selectedClass}`
  }
  return selected ? `bg-primary/5 ${selectedClass}` : 'bg-card'
}

watch(
  () => editor.workflowId.value,
  () => {
    selectedNodeId.value = editor.nodes.value[0]?.id ?? ''
    selectedEdgeId.value = ''
    nodeRoleError.value = ''
    scheduleFitView(0, 80)
  },
  { flush: 'post' },
)

watch(
  () => editor.nodes.value.length,
  length => {
    if (length === 0) newNodeRole.value = 'input'
  },
  { immediate: true },
)

onMounted(async () => {
  await editor.refreshTargets()
  editor.startPolling()
  await nextTick()

  const host = canvasHost.value
  if (host && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(entries => {
      const entry = entries[0]
      if (!entry) return
      const { width, height } = entry.contentRect
      if (width <= 0 || height <= 0) return
      if (
        Math.abs(width - lastCanvasWidth) < 1
        && Math.abs(height - lastCanvasHeight) < 1
      ) return

      lastCanvasWidth = width
      lastCanvasHeight = height
      // Sidebar collapse/expand and native window resizing both end up here.
      scheduleFitView(120, 120)
    })
    resizeObserver.observe(host)
  }

  scheduleFitView(0, 0)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  cancelScheduledFit()
})
</script>

<template>
  <section class="flex min-h-0 w-full flex-1 flex-col gap-3">
    <header class="flex min-h-10 items-start justify-between gap-4">
      <div>
        <h1 class="m-0 text-xl leading-7 font-medium tracking-[-0.02em]">AI 工作台</h1>
        <p class="mt-1 mb-0 text-xs leading-[18px] text-muted-foreground">
          Vue Flow 与 AI 共用同一份 Workflow Definition。拖拽编辑、AI 生成和 Runtime 执行不会产生第二套流程数据。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <Button variant="outline" size="sm" :disabled="!editor.canUndo.value" title="撤销" @click="editor.undo()">
          <Undo2 :size="14" />撤销
        </Button>
        <Button variant="outline" size="sm" :disabled="!editor.canRedo.value" title="重做" @click="editor.redo()">
          <Redo2 :size="14" />重做
        </Button>
        <Button variant="outline" size="sm" :disabled="editor.busy.value" @click="editor.validate()">
          <CheckCircle2 :size="14" />验证
        </Button>
        <Button size="sm" :disabled="editor.busy.value || !editor.targetId.value" @click="editor.save()">
          <Save :size="14" />保存
        </Button>
      </div>
    </header>

    <div v-if="editor.error.value" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
      {{ editor.error.value }}
    </div>

    <div class="grid grid-cols-[minmax(220px,0.8fr)_minmax(240px,1fr)_auto] items-end gap-3 rounded-lg border border-border bg-card p-3">
      <label class="field">
        <span>Workspace Target</span>
        <select :value="editor.targetId.value" @change="changeTarget">
          <option v-for="target in editor.targets.value" :key="target.target_id" :value="target.target_id">
            {{ target.service_name }} / {{ target.profile_name }}
          </option>
        </select>
      </label>
      <label class="field">
        <span>Workflow</span>
        <select :value="editor.workflowId.value" @change="changeWorkflow">
          <option v-for="workflow in editor.catalog.value.workflows" :key="workflow.id" :value="workflow.id">
            {{ workflow.name }} · v{{ workflow.version }} · {{ workflow.scope }}
          </option>
        </select>
      </label>
      <div class="flex items-center gap-2">
        <Button variant="outline" size="sm" @click="editor.newWorkflow()"><Plus :size="14" />新建</Button>
        <Button
          variant="outline"
          size="sm"
          class="text-destructive hover:bg-destructive/10 hover:text-destructive"
          :disabled="editor.busy.value || !canDeleteWorkflow"
          :title="canDeleteWorkflow ? '删除当前 Workspace Workflow' : '内置 Workflow 或未保存 Workflow 不可删除'"
          @click="removeWorkflow"
        ><Trash2 :size="14" />删除</Button>
      </div>
    </div>

    <div ref="canvasHost" class="relative min-h-[560px] flex-1 overflow-hidden rounded-lg border border-border bg-background">
        <div class="absolute top-3 left-3 z-20 flex items-center gap-2">
          <Button variant="outline" size="sm" class="bg-background/95 shadow-sm" @click="nodeLibraryOpen = true">
            <PanelLeftOpen :size="14" />节点库
          </Button>
          <Button variant="outline" size="sm" class="bg-background/95 shadow-sm" title="适配全部节点" @click="fitCanvas">
            <Scan :size="14" />适配视图
          </Button>
        </div>
        <div class="absolute top-3 right-3 z-20">
          <Button variant="outline" size="sm" class="bg-background/95 shadow-sm" @click="inspectorOpen = true">
            <PanelRightOpen :size="14" />Inspector
          </Button>
        </div>

        <VueFlow
          v-model:nodes="editor.nodes.value"
          v-model:edges="editor.edges.value"
          :min-zoom="0.3"
          :max-zoom="1.8"
          class="absolute inset-0 h-full w-full"
          @connect="onConnect"
          @node-click="({ node }) => selectNode(node as WorkflowCanvasNode)"
          @edge-click="({ edge }) => selectEdge(edge as WorkflowCanvasEdge)"
          @pane-click="clearSelection"
        >
          <template #node-workflow="{ id, data, selected }">
            <div :class="['relative min-w-[170px] rounded-lg border px-3 py-2 shadow-sm transition-colors', nodeKindBorderClass(data.kind), nodeStateClass(data.status, selected), nodeValidationClass(id)]">
              <Handle
                v-if="nodeRole(id, data.config) !== 'input'"
                class="workflow-handle workflow-handle-target"
                type="target"
                :position="Position.Left"
              />
              <div class="flex items-center gap-2">
                <component :is="nodeIcon(data.kind)" :size="14" class="text-muted-foreground" />
                <strong class="text-[11px] font-medium">{{ data.label }}</strong>
              </div>
              <div class="mt-1 font-mono text-[9px] uppercase tracking-wide text-muted-foreground">
                {{ nodeRole(id, data.config) }} · {{ data.kind }}<span v-if="data.status !== 'idle'"> · {{ data.status }}</span>
              </div>
              <span
                v-if="validationIssues(id).length"
                class="absolute -top-2 -right-2 flex h-5 min-w-5 items-center justify-center rounded-full border border-background bg-destructive px-1 text-[9px] font-medium text-destructive-foreground"
                :title="validationIssues(id).map(item => item.message).join('\n')"
              >{{ validationIssues(id).length }}</span>
              <Handle
                v-if="nodeRole(id, data.config) !== 'output'"
                class="workflow-handle workflow-handle-source"
                type="source"
                :position="Position.Right"
              />
            </div>
          </template>
        </VueFlow>
        <button
          v-if="!editor.nodes.value.length"
          type="button"
          class="absolute top-1/2 left-1/2 z-10 grid -translate-x-1/2 -translate-y-1/2 gap-1 rounded-lg border border-dashed border-border bg-background/95 px-6 py-5 text-center hover:bg-secondary"
          @click="nodeLibraryOpen = true"
        >
          <strong class="text-xs font-medium">从节点库开始编排</strong>
          <span class="text-[10px] leading-4 text-muted-foreground">添加 Skill、Tool 或 Flow Control 节点</span>
        </button>
        <div class="pointer-events-none absolute right-3 bottom-3 z-20 rounded-md border border-border bg-background/90 px-2 py-1 font-mono text-[10px] text-muted-foreground shadow-sm">
          {{ editor.nodes.value.length }} Nodes · {{ editor.edges.value.length }} Edges
        </div>

      <Sheet :open="nodeLibraryOpen" side="left" title="Node Library" width-class="w-[280px]" @close="nodeLibraryOpen = false">
        <label class="field mb-3">
          <span>搜索节点</span>
          <div class="relative">
            <Search :size="13" class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" />
            <input v-model="nodeLibraryQuery" class="pl-8" placeholder="Skill、Tool、Approval..." />
          </div>
        </label>
        <div class="mb-3">
          <div class="mb-2 text-[10px] font-medium text-muted-foreground">新增节点角色</div>
          <div class="grid grid-cols-3 gap-1.5">
            <Button
              v-for="role in nodeRoles"
              :key="role"
              :variant="newNodeRole === role ? 'secondary' : 'outline'"
              size="sm"
              class="h-7 px-2 text-[10px]"
              @click="newNodeRole = role"
            >
              {{ nodeRoleLabel(role) }}
            </Button>
          </div>
          <div class="mt-1.5 text-[9px] leading-4 text-muted-foreground">
            输入：仅输出；处理：输入 + 输出；输出：仅输入。
          </div>
        </div>
        <div class="grid gap-4">
          <div v-for="group in filteredNodeGroups" :key="group.label" class="grid gap-2">
            <div>
              <div class="text-[10px] font-medium">{{ group.label }}</div>
              <div class="mt-0.5 text-[9px] leading-4 text-muted-foreground">{{ group.description }}</div>
            </div>
            <button
              v-for="kind in group.kinds"
              :key="kind"
              type="button"
              class="flex w-full items-start justify-start gap-2 rounded-md border border-border bg-background px-2.5 py-2 text-left hover:bg-secondary"
              @click="addNode(kind)"
            >
              <component :is="nodeIcon(kind)" class="mt-0.5 flex-none text-muted-foreground" :size="14" />
              <span class="grid gap-0.5 text-left">
                <strong class="text-[11px] font-medium">{{ nodeDefinitions[kind].label }}</strong>
                <small class="text-[10px] leading-4 text-muted-foreground">{{ nodeDefinitions[kind].description }}</small>
              </span>
            </button>
          </div>
          <div v-if="!filteredNodeGroups.length" class="rounded-md border border-dashed border-border px-3 py-3 text-[10px] leading-4 text-muted-foreground">
            没有匹配的节点类型。
          </div>
        </div>

        <div class="mt-5 border-t border-border pt-3">
          <div class="mb-2 text-xs font-medium">Run</div>
          <select
            class="w-full rounded-md border border-input bg-background px-2 py-1.5 text-[11px]"
            :value="editor.selectedRunId.value"
            @change="editor.selectedRunId.value = ($event.target as HTMLSelectElement).value; editor.refreshRuntimeState()"
          >
            <option value="">无运行记录</option>
            <option
              v-for="run in editor.runs.value.filter(item => item.workflow_id === editor.workflowId.value)"
              :key="run.run_id"
              :value="run.run_id"
            >{{ run.status }} · {{ run.run_id.slice(0, 8) }}</option>
          </select>
          <div v-if="editor.selectedRun.value" class="mt-2 rounded-md border border-border bg-secondary/40 p-2 text-[10px] leading-4 text-muted-foreground">
            <div>状态：{{ editor.selectedRun.value.status }}</div>
            <div>Definition：v{{ editor.selectedRun.value.workflow_version }}</div>
            <div v-if="editor.selectedRun.value.error" class="mt-1 text-destructive">{{ editor.selectedRun.value.error }}</div>
          </div>
        </div>
      </Sheet>

      <Sheet :open="inspectorOpen" side="right" title="Inspector" width-class="w-[340px]" @close="inspectorOpen = false">
        <div class="mb-3 flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 text-xs font-medium"><Braces :size="14" />当前选择</div>
          <div class="flex items-center gap-1">
            <Button v-if="selectedNode" variant="ghost" size="icon" class="h-7 w-7" title="复制节点" @click="duplicateSelectedNode"><Copy :size="13" /></Button>
            <Button v-if="selectedNode || selectedEdge" variant="ghost" size="icon" class="h-7 w-7 text-destructive" title="删除" @click="removeSelected"><Trash2 :size="13" /></Button>
          </div>
        </div>

        <div v-if="selectedNode" class="grid gap-3">
          <div v-if="validationIssues(selectedNode.id).length" class="grid gap-1 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-[10px] leading-4 text-destructive">
            <div v-for="issue in validationIssues(selectedNode.id)" :key="`${issue.code}-${issue.message}`">
              {{ issue.message }}
            </div>
          </div>
          <label class="field"><span>名称</span><input v-model="selectedNode.data.label" /></label>
          <label class="field"><span>Node ID</span><input :value="selectedNode.id" disabled /></label>
          <label class="field">
            <span>节点角色</span>
            <select :value="selectedNodeRole" @change="changeSelectedNodeRole">
              <option value="input">输入节点</option>
              <option value="process">处理节点</option>
              <option value="output">输出节点</option>
            </select>
          </label>
          <div v-if="nodeRoleError" class="text-[10px] leading-4 text-destructive">{{ nodeRoleError }}</div>

          <label v-if="['prompt', 'skill', 'tool'].includes(selectedNode.data.kind)" class="field">
            <span>搜索能力</span>
            <div class="relative">
              <Search :size="13" class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" />
              <input v-model="capabilityQuery" class="pl-8" placeholder="按名称、ID 或说明筛选" />
            </div>
          </label>

          <template v-if="selectedNode.data.kind === 'prompt'">
            <label class="field">
              <span>Prompt</span>
              <select :value="selectedNode.data.config.prompt_id" @change="setNodeConfig('prompt_id', ($event.target as HTMLSelectElement).value)">
                <option v-for="prompt in filteredPrompts" :key="prompt.id" :value="prompt.id">{{ prompt.name }}</option>
              </select>
            </label>
            <div v-if="selectedPrompt" class="text-[10px] leading-4 text-muted-foreground">{{ selectedPrompt.description }}</div>
            <div v-else-if="selectedNode.data.config.prompt_id" class="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-[10px] leading-4 text-destructive">
              当前 Prompt 引用已失效。请重新选择 Prompt。
            </div>
          </template>

          <template v-if="selectedNode.data.kind === 'skill'">
            <label class="field">
              <span>Skill</span>
              <select :value="selectedNode.data.config.skill_id" @change="setNodeConfig('skill_id', ($event.target as HTMLSelectElement).value)">
                <option v-for="skill in filteredSkills" :key="skill.id" :value="skill.id">{{ skill.name }}</option>
              </select>
            </label>
            <div v-if="selectedSkill" class="rounded-md border border-border bg-secondary/40 p-2 text-[10px] leading-4 text-muted-foreground">
              <div>{{ selectedSkill.description }}</div>
              <div class="mt-1 font-mono">
                Tools:
                {{ selectedSkill.tool_references.map(item => item.provider === 'mcp' ? `mcp:${item.connection_id}:${item.tool_name}` : `system:${item.tool_name}`).join(', ') || 'none' }}
              </div>
            </div>
            <div v-else-if="selectedNode.data.config.skill_id" class="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-[10px] leading-4 text-destructive">
              当前 Skill 引用已失效。请重新选择 Skill。
            </div>
          </template>

          <template v-if="selectedNode.data.kind === 'tool'">
            <label class="field">
              <span>Provider</span>
              <select :value="selectedToolProvider" @change="changeToolProvider">
                <option value="system">System</option>
                <option value="mcp">MCP</option>
              </select>
            </label>
            <template v-if="selectedToolProvider === 'system'">
              <label class="field">
                <span>Tool</span>
                <select
                  :value="selectedNode.data.config.tool_name"
                  @change="setToolReference('system', undefined, ($event.target as HTMLSelectElement).value)"
                >
                  <option
                    v-for="tool in filteredSystemTools"
                    :key="tool.key"
                    :value="tool.tool_name"
                  >{{ tool.tool_name }}</option>
                </select>
              </label>
            </template>
            <template v-else>
              <label class="field">
                <span>MCP Connection</span>
                <select :value="selectedNode.data.config.connection_id" @change="changeMcpConnection">
                  <option
                    v-for="connection in enabledMcpConnections"
                    :key="connection.id"
                    :value="connection.id"
                  >{{ connection.name }}</option>
                </select>
              </label>
              <label class="field">
                <span>Tool</span>
                <select
                  :value="selectedNode.data.config.tool_name"
                  @change="setToolReference('mcp', String(selectedNode.data.config.connection_id ?? ''), ($event.target as HTMLSelectElement).value)"
                >
                  <option
                    v-for="tool in filteredMcpTools(String(selectedNode.data.config.connection_id ?? ''))"
                    :key="tool.key"
                    :value="tool.tool_name"
                  >{{ tool.tool_name }}</option>
                </select>
              </label>
              <div
                v-if="!enabledMcpConnections.length"
                class="rounded-md border border-border bg-secondary/40 p-2 text-[10px] leading-4 text-muted-foreground"
              >
                当前没有启用的 MCP Connection。请先在「MCP 服务」中添加并发现 Tools。
              </div>
            </template>
            <div v-if="selectedTool" class="rounded-md border border-border bg-secondary/40 p-2 text-[10px] leading-4 text-muted-foreground">
              <div>{{ selectedTool.description || 'No description' }}</div>
              <div class="mt-1 font-mono">{{ selectedTool.key }}</div>
            </div>
            <div
              v-else-if="selectedNode.data.config.tool_name"
              class="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-[10px] leading-4 text-destructive"
            >
              当前 Tool 引用不在 Effective Tool Catalog 中。MCP Connection 可能已禁用、删除或尚未完成 Tool Discovery。
            </div>
          </template>

          <template v-if="['prompt', 'skill', 'tool'].includes(selectedNode.data.kind)">
            <div class="grid gap-2">
              <div class="text-[10px] font-medium text-muted-foreground">Arguments</div>
              <SchemaValueEditor
                :schema="selectedArgumentSchema"
                :model-value="selectedArguments"
                @update:model-value="setVisualArguments"
              />
            </div>
            <details class="rounded-md border border-border bg-secondary/20 p-2">
              <summary class="cursor-pointer text-[10px] font-medium text-muted-foreground">Advanced JSON</summary>
              <textarea
                class="mt-2 min-h-28 w-full resize-y rounded-md border border-input bg-background px-2.5 py-2 font-mono text-[10px] outline-none focus:ring-2 focus:ring-ring/30"
                :value="argumentsText()"
                spellcheck="false"
                @change="setArguments"
              />
            </details>
          </template>

          <template v-if="selectedNode.data.kind === 'approval'">
            <label class="field"><span>标题</span><input :value="selectedNode.data.config.title" @input="setNodeConfig('title', ($event.target as HTMLInputElement).value)" /></label>
            <label class="field"><span>说明</span><input :value="selectedNode.data.config.description" @input="setNodeConfig('description', ($event.target as HTMLInputElement).value)" /></label>
          </template>

          <label v-if="selectedNode.data.kind === 'condition'" class="field">
            <span>受限表达式</span>
            <input :value="selectedNode.data.config.expression" @input="setNodeConfig('expression', ($event.target as HTMLInputElement).value)" />
          </label>

          <template v-if="selectedNode.data.kind === 'artifact'">
            <label class="field"><span>Artifact ID</span><input :value="selectedNode.data.config.artifact_id" @input="setNodeConfig('artifact_id', ($event.target as HTMLInputElement).value)" /></label>
            <label class="field">
              <span>Source Node</span>
              <select :value="selectedNode.data.config.source_node_id" @change="setNodeConfig('source_node_id', ($event.target as HTMLSelectElement).value)">
                <option v-for="node in editor.nodes.value.filter(item => item.id !== selectedNode?.id)" :key="node.id" :value="node.id">{{ node.data.label }}</option>
              </select>
            </label>
            <label class="field">
              <span>Format</span>
              <select :value="selectedNode.data.config.format" @change="setNodeConfig('format', ($event.target as HTMLSelectElement).value)">
                <option value="json">json</option><option value="text">text</option>
              </select>
            </label>
          </template>

          <label class="field">
            <span>on_error</span>
            <select v-model="selectedNode.data.policy.on_error"><option value="stop">stop</option><option value="continue">continue</option></select>
          </label>
          <div v-if="configError" class="text-[10px] text-destructive">{{ configError }}</div>
        </div>

        <div v-else-if="selectedEdge" class="grid gap-3">
          <div v-if="validationIssues(selectedEdge.id).length" class="grid gap-1 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-[10px] leading-4 text-destructive">
            <div v-for="issue in validationIssues(selectedEdge.id)" :key="`${issue.code}-${issue.message}`">
              {{ issue.message }}
            </div>
          </div>
          <label class="field"><span>Edge</span><input :value="selectedEdge.id" disabled /></label>
          <label class="field">
            <span>Condition</span>
            <select :value="selectedEdge.data?.condition" @change="setEdgeCondition(($event.target as HTMLSelectElement).value as WorkflowEdgeDto['condition'])">
              <option v-for="condition in edgeConditions" :key="condition" :value="condition">{{ condition }}</option>
            </select>
          </label>
        </div>

        <div v-else class="grid gap-3">
          <label class="field"><span>Workflow ID</span><input v-model="editor.workflowId.value" /></label>
          <label class="field"><span>名称</span><input v-model="editor.workflowName.value" /></label>
          <label class="field"><span>说明（AI Discovery 必填）</span><input v-model="editor.workflowDescription.value" /></label>
          <label class="field">
            <span>Tags（逗号分隔）</span>
            <input
              :value="editor.workflowTags.value.join(', ')"
              placeholder="frontend, review"
              @change="setWorkflowTags"
            />
          </label>
          <div class="grid gap-2">
            <div class="text-[10px] font-medium text-muted-foreground">Inputs Schema</div>
            <ObjectSchemaBuilder v-model="editor.workflowInputsSchema.value" />
          </div>
          <details class="rounded-md border border-border bg-secondary/20 p-2">
            <summary class="cursor-pointer text-[10px] font-medium text-muted-foreground">Advanced JSON Schema</summary>
            <textarea
              class="mt-2 min-h-40 w-full resize-y rounded-md border border-input bg-background px-2.5 py-2 font-mono text-[10px] outline-none focus:ring-2 focus:ring-ring/30"
              :value="workflowInputsSchemaText()"
              spellcheck="false"
              @change="setWorkflowInputsSchema"
            />
          </details>
          <div v-if="workflowSchemaError" class="text-[10px] leading-4 text-destructive">{{ workflowSchemaError }}</div>
          <div class="rounded-md border border-border bg-secondary/40 p-2 text-[10px] leading-4 text-muted-foreground">
            Description、Tags 与 Inputs Schema 会进入 workflow_list，供 AI 判断 Workflow 用途与调用参数。坐标只决定 Vue Flow 布局；Runtime 执行顺序只由 Entry Node、Edge 和 Edge Condition 决定。
          </div>
        </div>

        <div v-if="editor.workflowApprovals.value.length" class="mt-5 border-t border-border pt-3">
          <div class="mb-2 flex items-center gap-2 text-xs font-medium"><ShieldCheck :size="14" />待人工审批</div>
          <div v-for="approval in editor.workflowApprovals.value" :key="approval.request_id" class="mb-2 rounded-md border border-yellow-400/60 bg-yellow-400/10 p-2">
            <div class="text-[11px] font-medium">{{ approval.title }}</div>
            <div class="mt-1 text-[10px] leading-4 text-muted-foreground">{{ approval.description || approval.node_id }}</div>
            <div class="mt-2 flex gap-1.5">
              <Button size="sm" class="h-7 bg-yellow-400 px-2 text-[10px] text-black hover:bg-yellow-300" @click="editor.respondApproval(approval.request_id, true)"><Check :size="12" />批准</Button>
              <Button variant="outline" size="sm" class="h-7 px-2 text-[10px]" @click="editor.respondApproval(approval.request_id, false)"><X :size="12" />拒绝</Button>
            </div>
          </div>
        </div>
      </Sheet>
    </div>

    <div class="flex min-h-9 items-center justify-between gap-3 rounded-md border border-border bg-secondary/40 px-3 py-2 text-[11px]">
      <span class="min-w-0 text-muted-foreground">{{ editor.notice.value || '保存前始终使用 Python Workflow Validator 验证。' }}</span>
      <div class="flex flex-none items-center gap-2 font-mono text-[10px] text-muted-foreground">
        <span>{{ editor.workflowScope.value }}</span><span>v{{ editor.workflowVersion.value }}</span>
        <span v-if="editor.selectedTarget.value?.running" class="text-green-500">Runtime Running</span>
        <span v-else class="flex items-center gap-1"><CircleStop :size="10" />Runtime Stopped</span>
      </div>
    </div>
  </section>
</template>

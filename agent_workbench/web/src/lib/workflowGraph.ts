import type {
  WorkflowDefinitionDto,
  WorkflowEdgeDto,
  WorkflowNodeDto,
  WorkflowNodeKind,
} from '../types'

export interface WorkflowCanvasNodeData extends Record<string, unknown> {
  label: string
  kind: WorkflowNodeKind
  config: Record<string, unknown>
  policy: WorkflowNodeDto['policy']
  status: string
}

export interface WorkflowCanvasEdgeData extends Record<string, unknown> {
  condition: WorkflowEdgeDto['condition']
}

export interface WorkflowCanvasNode {
  id: string
  type: string
  data: WorkflowCanvasNodeData
  position: { x: number; y: number }
}

export interface WorkflowCanvasEdge {
  id: string
  source: string
  target: string
  label?: string
  data: WorkflowCanvasEdgeData
}

export interface WorkflowGraphMeta {
  id: string
  name: string
  description: string
  version: number
  entryNodeId: string
  inputsSchema: Record<string, unknown>
  tags: string[]
  metadata: Record<string, unknown>
}

export function workflowToCanvas(value: WorkflowDefinitionDto): {
  nodes: WorkflowCanvasNode[]
  edges: WorkflowCanvasEdge[]
} {
  return {
    nodes: value.nodes.map(node => ({
      id: node.id,
      type: 'workflow',
      position: { ...node.position },
      data: {
        label: node.name,
        kind: node.type,
        config: { ...node.config },
        policy: { ...node.policy },
        status: 'idle',
      },
    })),
    edges: value.edges.map(edge => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.condition,
      data: { condition: edge.condition },
    })),
  }
}

export function canvasToWorkflow(
  meta: WorkflowGraphMeta,
  nodes: WorkflowCanvasNode[],
  edges: WorkflowCanvasEdge[],
): WorkflowDefinitionDto {
  return {
    schema_version: 1,
    id: meta.id.trim(),
    name: meta.name.trim() || meta.id.trim(),
    description: meta.description.trim(),
    version: meta.version || 1,
    entry_node_id: meta.entryNodeId,
    inputs_schema: { ...meta.inputsSchema },
    tags: [...meta.tags],
    nodes: nodes.map(node => ({
      id: node.id,
      type: node.data.kind,
      name: node.data.label,
      position: { x: node.position.x, y: node.position.y },
      config: { ...node.data.config },
      policy: { ...node.data.policy },
    })),
    edges: edges.map(edge => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      condition: edge.data.condition,
    })),
    metadata: { ...meta.metadata },
  }
}

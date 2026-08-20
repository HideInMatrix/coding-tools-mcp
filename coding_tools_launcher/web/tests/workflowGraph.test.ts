import assert from 'node:assert/strict'
import test from 'node:test'

import { canvasToWorkflow, workflowToCanvas } from '../src/lib/workflowGraph.ts'
import type { WorkflowDefinitionDto } from '../src/types.ts'

const definition: WorkflowDefinitionDto = {
  schema_version: 1,
  id: 'graph-test',
  name: 'Graph Test',
  description: 'round trip',
  version: 3,
  entry_node_id: 'prompt',
  inputs_schema: {
    type: 'object',
    properties: { goal: { type: 'string' } },
    required: ['goal'],
    additionalProperties: false,
  },
  tags: ['analysis', 'test'],
  nodes: [
    {
      id: 'prompt',
      type: 'prompt',
      name: 'Prompt',
      position: { x: 10, y: 20 },
      config: { prompt_id: 'project-analysis', arguments: { goal: 'map' } },
      policy: { approval: 'none', on_error: 'stop' },
    },
    {
      id: 'approval',
      type: 'approval',
      name: 'Approve',
      position: { x: 300, y: 20 },
      config: { title: 'Continue' },
      policy: { approval: 'required', on_error: 'stop' },
    },
  ],
  edges: [
    {
      id: 'prompt-approval',
      source: 'prompt',
      target: 'approval',
      condition: 'success',
    },
  ],
  metadata: { owner: 'ai' },
}

test('Workflow Definition round-trips through the Vue Flow canvas model', () => {
  const canvas = workflowToCanvas(definition)
  canvas.nodes[0].position.x = 88
  canvas.edges[0].data.condition = 'failure'

  const rebuilt = canvasToWorkflow(
    {
      id: definition.id,
      name: definition.name,
      description: definition.description,
      version: definition.version,
      entryNodeId: definition.entry_node_id,
      inputsSchema: definition.inputs_schema,
      tags: definition.tags,
      metadata: definition.metadata,
    },
    canvas.nodes,
    canvas.edges,
  )

  assert.equal(rebuilt.nodes[0].position.x, 88)
  assert.equal(rebuilt.nodes[0].config.prompt_id, 'project-analysis')
  assert.equal(rebuilt.nodes[1].policy.approval, 'required')
  assert.equal(rebuilt.edges[0].condition, 'failure')
  assert.equal(rebuilt.entry_node_id, 'prompt')
  assert.deepEqual(rebuilt.inputs_schema, definition.inputs_schema)
  assert.deepEqual(rebuilt.tags, definition.tags)
  assert.deepEqual(rebuilt.metadata, definition.metadata)
})

test('System and MCP Tool references round-trip without embedding capability definitions', () => {
  const toolDefinition: WorkflowDefinitionDto = {
    schema_version: 1,
    id: 'tool-reference-test',
    name: 'Tool Reference Test',
    description: 'tool refs',
    version: 1,
    entry_node_id: 'legacy-system',
    inputs_schema: { type: 'object', additionalProperties: true },
    tags: [],
    nodes: [
      {
        id: 'legacy-system',
        type: 'tool',
        name: 'Legacy System Tool',
        position: { x: 0, y: 0 },
        config: { tool_name: 'read_file', arguments: { path: 'README.md' } },
        policy: { approval: 'none', on_error: 'stop' },
      },
      {
        id: 'external-tool',
        type: 'tool',
        name: 'External Tool',
        position: { x: 260, y: 0 },
        config: {
          provider: 'mcp',
          connection_id: 'github',
          tool_name: 'create_issue',
          arguments: { title: 'Bug' },
        },
        policy: { approval: 'none', on_error: 'stop' },
      },
    ],
    edges: [
      {
        id: 'legacy-external',
        source: 'legacy-system',
        target: 'external-tool',
        condition: 'success',
      },
    ],
    metadata: {},
  }

  const canvas = workflowToCanvas(toolDefinition)
  const rebuilt = canvasToWorkflow(
    {
      id: toolDefinition.id,
      name: toolDefinition.name,
      description: toolDefinition.description,
      version: toolDefinition.version,
      entryNodeId: toolDefinition.entry_node_id,
      inputsSchema: toolDefinition.inputs_schema,
      tags: toolDefinition.tags,
      metadata: toolDefinition.metadata,
    },
    canvas.nodes,
    canvas.edges,
  )

  assert.equal(rebuilt.nodes[0].config.tool_name, 'read_file')
  assert.equal(rebuilt.nodes[0].config.provider, undefined)
  assert.equal(rebuilt.nodes[1].config.provider, 'mcp')
  assert.equal(rebuilt.nodes[1].config.connection_id, 'github')
  assert.equal(rebuilt.nodes[1].config.tool_name, 'create_issue')
})

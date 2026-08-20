import assert from 'node:assert/strict'
import test from 'node:test'

import { isSelectedResourceStarting, isSelectedServerStarting } from '../src/lib/serverState.ts'

test('a clean install with no selected server is not treated as starting', () => {
  assert.equal(isSelectedServerStarting('', ''), false)
  assert.equal(isSelectedResourceStarting('', ''), false)
})

test('only the selected non-empty server can be treated as starting', () => {
  assert.equal(isSelectedResourceStarting('server-a', 'server-a'), true)
  assert.equal(isSelectedResourceStarting('server-a', ''), false)
  assert.equal(isSelectedResourceStarting('server-a', 'server-b'), false)
  assert.equal(isSelectedResourceStarting('', 'server-a'), false)
})

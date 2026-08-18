import assert from 'node:assert/strict'
import test from 'node:test'

import { isSelectedServerStarting } from '../src/lib/serverState.ts'

test('a clean install with no selected server is not treated as starting', () => {
  assert.equal(isSelectedServerStarting('', ''), false)
})

test('only the selected non-empty server can be treated as starting', () => {
  assert.equal(isSelectedServerStarting('server-a', 'server-a'), true)
  assert.equal(isSelectedServerStarting('server-a', ''), false)
  assert.equal(isSelectedServerStarting('server-a', 'server-b'), false)
})

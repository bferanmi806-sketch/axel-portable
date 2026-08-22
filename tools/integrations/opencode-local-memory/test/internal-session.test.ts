import assert from "node:assert/strict"
import { test } from "node:test"

import { INTERNAL_SESSION_PREFIX, InternalSessionRegistry, readSessionID } from "../src/internal-session.js"

test("internal sessions are identified by reserved prefix or registry", () => {
  const registry = new InternalSessionRegistry()
  registry.mark("ses-created-by-consolidator")

  assert.equal(registry.has(`${INTERNAL_SESSION_PREFIX}123`), true)
  assert.equal(registry.has("ses-created-by-consolidator"), true)
  assert.equal(registry.has("ses-user"), false)
})

test("session IDs are read from hook and event payloads", () => {
  assert.equal(readSessionID({ sessionID: "ses-direct" }), "ses-direct")
  assert.equal(readSessionID({ properties: { sessionID: "ses-nested" } }), "ses-nested")
  assert.equal(readSessionID({ event: { properties: { sessionID: "ses-event" } } }), "ses-event")
  assert.equal(readSessionID({ info: { sessionID: "ses-message" } }), "ses-message")
  assert.equal(readSessionID({ properties: { info: { id: "ses-created" } } }), "ses-created")
  assert.equal(readSessionID({ properties: null }), undefined)
})

test("internally titled sessions are excluded before their ID is registered", () => {
  const registry = new InternalSessionRegistry()
  assert.equal(registry.isInternalValue({ event: { properties: { info: { title: `${INTERNAL_SESSION_PREFIX}fixture` } } } }), true)
})

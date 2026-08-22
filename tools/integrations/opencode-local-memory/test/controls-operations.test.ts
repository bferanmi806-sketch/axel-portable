import assert from "node:assert/strict"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

import { Ledger } from "../src/capture/ledger.js"
import { CapturePolicy } from "../src/capture/policy.js"
import { ConsolidationService } from "../src/consolidation/service.js"
import type { ConsolidationRunner } from "../src/consolidation/types.js"
import { MemoryControlService } from "../src/controls/service.js"
import { createBackup } from "../src/operations.js"

async function seeded() {
  const root = mkdtempSync(join(tmpdir(), "memory-controls-"))
  const ledger = new Ledger({ path: join(root, "memory.sqlite3") })
  const capture = new CapturePolicy().sanitize({ id: "evt-control", sessionID: "ses-control", projectID: "unscoped", eventType: "message", source: "message", payload: "Decision: keep source links." })
  assert.equal(capture.kind, "accepted")
  if (capture.kind === "accepted") await ledger.append(capture.capture)
  const runner: ConsolidationRunner = { async run(input) { return { model: "fixture/model", output: JSON.stringify({ assertions: [{ scope: "project", category: "decision", content: "Keep source links.", confidence: 0.9, sourceEventIDs: input.events.map((event) => event.id) }] }) } } }
  await new ConsolidationService(ledger, runner).consolidate("ses-control")
  return { root, ledger }
}

test("inspect, correction, preview, confirmed forget, and rebuild are source-aware", async () => {
  const { root, ledger } = await seeded()
  const controls = new MemoryControlService(ledger)
  try {
    const original = ledger.assertions()[0]
    assert.ok(original)
    assert.deepEqual(controls.inspect(original.id)?.events.map((event) => event.id), ["evt-control"])
    const correction = await controls.correct(original.id, "Source links are mandatory.")
    assert.equal(ledger.assertions().find((item) => item.id === original.id)?.status, "superseded")
    assert.equal(correction.status, "current")
    await assert.rejects(() => controls.forget(["evt-control"], false), /requires confirmed/)
    assert.deepEqual(controls.previewForget(["evt-control"]), { eventIDs: ["evt-control"], assertionIDs: [original.id, correction.id] })
    await controls.forget(["evt-control"], true)
    assert.deepEqual(ledger.events(), [])
    assert.deepEqual(ledger.assertions(), [])
    await controls.rebuild()
    assert.deepEqual(ledger.assertions(), [])
  } finally {
    ledger.close()
    rmSync(root, { recursive: true, force: true })
  }
})

test("backup restores a readable equivalent ledger and status omits payload content", async () => {
  const { root, ledger } = await seeded()
  try {
    const backup = await createBackup(ledger, join(root, "backups"))
    const restored = new Ledger({ path: backup.database })
    try {
      assert.equal(restored.events().length, ledger.events().length)
      assert.equal(restored.assertions().length, ledger.assertions().length)
      const status = restored.status()
      assert.equal(status.events, 1)
      assert.equal(JSON.stringify(status).includes("Keep source links"), false)
    } finally {
      restored.close()
    }
  } finally {
    ledger.close()
    rmSync(root, { recursive: true, force: true })
  }
})

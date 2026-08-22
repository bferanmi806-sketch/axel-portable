import assert from "node:assert/strict"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { DatabaseSync } from "node:sqlite"
import { test } from "node:test"

import { Ledger } from "../src/capture/ledger.js"
import type { SanitizedCapture } from "../src/capture/types.js"

function createLedger(): { ledger: Ledger; root: string } {
  const root = mkdtempSync(join(tmpdir(), "memory-ledger-"))
  return { ledger: new Ledger({ path: join(root, "data", "memory.sqlite3") }), root }
}

function capture(id: string, sessionID = "ses-1"): SanitizedCapture {
  return {
    id,
    sessionID,
    projectID: "unscoped",
    eventType: "message",
    source: "message",
    occurredAt: "2026-07-26T00:00:00.000Z",
    payloadClass: "text",
    payload: '{"text":"sanitized"}',
    payloadHash: "fixture-hash",
    byteLength: 20,
    localReference: null,
    sanitizerActions: [],
  }
}

test("ledger assigns stable session order and deduplicates event IDs", async () => {
  const { ledger, root } = createLedger()
  try {
    const first = await ledger.append(capture("event-1"))
    const duplicate = await ledger.append(capture("event-1"))
    const second = await ledger.append(capture("event-2"))

    assert.deepEqual(first, { inserted: true, sequence: 1 })
    assert.deepEqual(duplicate, { inserted: false, sequence: 1 })
    assert.deepEqual(second, { inserted: true, sequence: 2 })
    assert.deepEqual(ledger.events().map((event) => event.sequence), [1, 2])
    assert.equal(ledger.integrityCheck(), "ok")
  } finally {
    ledger.close()
    rmSync(root, { recursive: true, force: true })
  }
})

test("serialized concurrent appends do not duplicate or corrupt session order", async () => {
  const { ledger, root } = createLedger()
  try {
    const writes = Array.from({ length: 30 }, (_, index) => ledger.append(capture(`event-${index}`)))
    await Promise.all(writes)

    assert.deepEqual(ledger.events("ses-1").map((event) => event.sequence), Array.from({ length: 30 }, (_, index) => index + 1))
    assert.equal(ledger.integrityCheck(), "ok")
  } finally {
    ledger.close()
    rmSync(root, { recursive: true, force: true })
  }
})

test("quarantine diagnostics retain a reason but no payload", async () => {
  const { ledger, root } = createLedger()
  try {
    await ledger.quarantine("payload normalization failed")
    assert.equal(ledger.diagnostics(), 1)
    assert.deepEqual(ledger.events(), [])
  } finally {
    ledger.close()
    rmSync(root, { recursive: true, force: true })
  }
})

test("a closed ledger reopens with committed events intact", async () => {
  const { ledger, root } = createLedger()
  const path = join(root, "data", "memory.sqlite3")
  let closed = false
  try {
    await ledger.append(capture("event-before-restart"))
    ledger.close()
    closed = true

    const reopened = new Ledger({ path })
    try {
      assert.deepEqual(reopened.events("ses-1").map((event) => event.id), ["event-before-restart"])
      assert.equal(reopened.integrityCheck(), "ok")
    } finally {
      reopened.close()
    }
  } finally {
    if (!closed) ledger.close()
    rmSync(root, { recursive: true, force: true })
  }
})

test("opening a second ledger does not interrupt a run owned by the first", async () => {
  const root = mkdtempSync(join(tmpdir(), "memory-ledger-owners-"))
  const path = join(root, "memory.sqlite3")
  const first = new Ledger({ path })
  try {
    await first.append(capture("event-owner"))
    const work = await first.prepareConsolidation("ses-1", 1024)
    assert.ok(work)

    const second = new Ledger({ path })
    try {
      assert.equal(second.consolidationRuns()[0]?.status, "running")
      assert.equal((await second.prepareConsolidation("ses-1", 1024)), undefined)
    } finally {
      second.close()
    }
  } finally {
    first.close()
    rmSync(root, { recursive: true, force: true })
  }
})

test("a newer schema is refused without attempting a write", () => {
  const { ledger, root } = createLedger()
  const path = join(root, "data", "memory.sqlite3")
  ledger.close()
  const raw = new DatabaseSync(path)
  raw.prepare("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)").run(999, "2026-07-26T00:00:00.000Z")
  raw.close()

  try {
    assert.throws(() => new Ledger({ path }), /newer than this plugin supports/)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test("a version-one ledger migrates its project registry without losing sessions", async () => {
  const root = mkdtempSync(join(tmpdir(), "memory-ledger-v1-"))
  const path = join(root, "memory.sqlite3")
  const legacy = new DatabaseSync(path)
  legacy.exec(`
    CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
    INSERT INTO schema_migrations VALUES (1, '2026-07-25T00:00:00.000Z');
    CREATE TABLE projects (id TEXT PRIMARY KEY, workspace TEXT UNIQUE);
    INSERT INTO projects VALUES ('unscoped', NULL);
    CREATE TABLE sessions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, created_at TEXT NOT NULL);
    INSERT INTO sessions VALUES ('ses-legacy', 'unscoped', '2026-07-25T00:00:00.000Z');
    CREATE TABLE events (
      id TEXT PRIMARY KEY, session_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_type TEXT NOT NULL,
      source TEXT NOT NULL, occurred_at TEXT NOT NULL, payload_class TEXT NOT NULL, payload TEXT,
      payload_hash TEXT NOT NULL, byte_length INTEGER NOT NULL, local_reference TEXT, sanitizer_actions TEXT NOT NULL,
      UNIQUE(session_id, sequence)
    );
    INSERT INTO events VALUES ('legacy-event', 'ses-legacy', 1, 'message', 'message', '2026-07-25T00:00:00.000Z', 'text', 'sanitized', 'hash', 9, NULL, '[]');
    CREATE TABLE diagnostics (id INTEGER PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE operational_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
  `)
  legacy.close()

  try {
    const ledger = new Ledger({ path })
    try {
      assert.equal(ledger.events("ses-legacy")[0]?.id, "legacy-event")
      assert.equal(ledger.findProject("unscoped")?.kind, "unscoped")
    } finally {
      ledger.close()
    }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

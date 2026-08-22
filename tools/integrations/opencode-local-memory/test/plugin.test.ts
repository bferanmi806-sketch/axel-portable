import assert from "node:assert/strict"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

import type { PluginInput } from "@opencode-ai/plugin"

import { InternalSessionRegistry } from "../src/internal-session.js"
import { Ledger } from "../src/capture/ledger.js"
import type { ConsolidationRunner } from "../src/consolidation/types.js"
import type { MemoryPorts, PluginLogger } from "../src/ports.js"
import { createLocalMemoryPlugin } from "../src/plugin.js"

function context(): PluginInput {
  return {
    client: { app: { log: async () => ({}) } },
    directory: "C:\\workspace",
    worktree: "C:\\workspace",
    project: {},
    experimental_workspace: { register() {} },
    serverUrl: new URL("http://localhost"),
    $: {},
  } as unknown as PluginInput
}

function logger(warnings: string[]): PluginLogger {
  return {
    async warn(message) {
      warnings.push(message)
    },
  }
}

test("disabled subsystems do not invoke ports", async () => {
  const calls: string[] = []
  const ports: MemoryPorts = {
    capture: { onEvent: async () => { calls.push("capture") } },
    consolidation: { onSessionBoundary: async () => { calls.push("consolidation") } },
    injection: { onSystem: async () => { calls.push("injection") } },
  }
  const hooks = await createLocalMemoryPlugin({ ports })(context(), {})

  await hooks.event?.({ event: { type: "session.idle", properties: { sessionID: "ses-user" } } } as never)
  await hooks["experimental.chat.system.transform"]?.({ sessionID: "ses-user" } as never, { system: [] })

  assert.deepEqual(calls, [])
})

test("each subsystem can be enabled independently", async () => {
  const calls: string[] = []
  const ports: MemoryPorts = {
    capture: { onEvent: async () => { calls.push("capture") } },
    consolidation: { onSessionBoundary: async () => { calls.push("consolidation") } },
    injection: { onSystem: async () => { calls.push("injection") } },
  }
  const plugin = createLocalMemoryPlugin({ ports })

  const captureHooks = await plugin(context(), { capture: true })
  await captureHooks.event?.({ event: { type: "session.created", properties: { info: {} } } } as never)

  const consolidationHooks = await plugin(context(), { consolidation: true })
  await consolidationHooks.event?.({ event: { type: "session.idle", properties: { sessionID: "ses-user" } } } as never)

  const injectionHooks = await plugin(context(), { injection: true })
  await injectionHooks["experimental.chat.system.transform"]?.({ sessionID: "ses-user" } as never, { system: [] })

  assert.deepEqual(calls, ["capture", "consolidation", "injection"])
})

test("port failures are swallowed and reported", async () => {
  const warnings: string[] = []
  const hooks = await createLocalMemoryPlugin({
    logger: logger(warnings),
    ports: { capture: { onEvent: async () => { throw new Error("fixture failure") } } },
  })(context(), { capture: true })

  await assert.doesNotReject(() =>
    hooks.event?.({ event: { type: "session.created", properties: { info: {} } } } as never) ?? Promise.resolve())
  assert.deepEqual(warnings, ["Memory subsystem operation failed"])
})

test("malformed events are ignored without invoking capture", async () => {
  let called = false
  const warnings: string[] = []
  const hooks = await createLocalMemoryPlugin({
    logger: logger(warnings),
    ports: { capture: { onEvent: async () => { called = true } } },
  })(context(), { capture: true })

  await hooks.event?.({ event: { type: "unknown.event" } } as never)

  assert.equal(called, false)
  assert.deepEqual(warnings, ["Ignored unsupported or malformed OpenCode event"])
})

test("internal sessions are excluded from every enabled boundary", async () => {
  const calls: string[] = []
  const internalSessions = new InternalSessionRegistry()
  internalSessions.mark("ses-internal")
  const hooks = await createLocalMemoryPlugin({
    internalSessions,
    ports: {
      capture: {
        onEvent: async () => { calls.push("event") },
        onToolBefore: async () => { calls.push("tool") },
        onMessages: async () => { calls.push("messages") },
      },
      consolidation: { onSessionBoundary: async () => { calls.push("consolidation") } },
      injection: { onSystem: async () => { calls.push("injection") } },
    },
  })(context(), { capture: true, consolidation: true, injection: true })

  await hooks.event?.({ event: { type: "session.idle", properties: { sessionID: "ses-internal" } } } as never)
  await hooks["tool.execute.before"]?.({ sessionID: "ses-internal" } as never, { args: {} })
  await hooks["experimental.chat.messages.transform"]?.({}, {
    messages: [{ info: { sessionID: "ses-internal" }, parts: [] }],
  } as never)
  await hooks["experimental.chat.system.transform"]?.({ sessionID: "ses-internal" } as never, { system: [] })

  assert.deepEqual(calls, [])
})

test("enabled default capture persists a sanitized event and disposes cleanly", async () => {
  const root = mkdtempSync(join(tmpdir(), "memory-plugin-"))
  const hooks = await createLocalMemoryPlugin()(context(), { capture: true, dataDir: root })

  try {
    await hooks.event?.({
      event: {
        type: "session.created",
        properties: { sessionID: "ses-user", authorization: "Bearer fixture-secret-token" },
      },
    } as never)
    await hooks.dispose?.()

    const ledger = new Ledger({ path: join(root, "memory.sqlite3") })
    try {
      const [event] = ledger.events("ses-user")
      assert.ok(event)
      assert.notEqual(event.projectID, "unscoped")
      assert.doesNotMatch(event.payload ?? "", /fixture-secret-token/)
      assert.match(event.payload ?? "", /\[REDACTED\]/)
    } finally {
      ledger.close()
    }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test("enabled default capture quarantines supported events without a session ID", async () => {
  const root = mkdtempSync(join(tmpdir(), "memory-plugin-"))
  const hooks = await createLocalMemoryPlugin()(context(), { capture: true, dataDir: root })

  try {
    await hooks.event?.({ event: { type: "session.created", properties: {} } } as never)
    await hooks.dispose?.()

    const ledger = new Ledger({ path: join(root, "memory.sqlite3") })
    try {
      assert.equal(ledger.diagnostics(), 1)
      assert.deepEqual(ledger.events(), [])
    } finally {
      ledger.close()
    }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test("project inspection tool is read-only and only available with an open capture ledger", async () => {
  const root = mkdtempSync(join(tmpdir(), "memory-plugin-"))
  const disabled = await createLocalMemoryPlugin()(context(), { dataDir: root })
  const enabled = await createLocalMemoryPlugin()(context(), { capture: true, dataDir: root })

  try {
    assert.equal(disabled.tool, undefined)
    assert.ok(enabled.tool?.memory_project_list)
  } finally {
    await enabled.dispose?.()
    rmSync(root, { recursive: true, force: true })
  }
})

test("session idle consolidates captured events through the injected runner", async () => {
  const root = mkdtempSync(join(tmpdir(), "memory-plugin-"))
  const runner: ConsolidationRunner = {
    async run(input) {
      return {
        model: "fixture/model",
        output: JSON.stringify({
          assertions: [{
            scope: "project",
            category: "decision",
            content: "A session idle boundary was consolidated.",
            confidence: 0.9,
            sourceEventIDs: input.events.map((event) => event.id),
          }],
        }),
      }
    },
  }
  const hooks = await createLocalMemoryPlugin({ consolidationRunner: runner })(context(), {
    capture: true,
    consolidation: true,
    dataDir: root,
  })

  try {
    await hooks.event?.({ event: { type: "session.created", properties: { sessionID: "ses-user" } } } as never)
    await hooks.event?.({ event: { type: "session.idle", properties: { sessionID: "ses-user" } } } as never)
    await hooks["experimental.chat.system.transform"]?.({ sessionID: "ses-user" } as never, { system: [] })
    await hooks.dispose?.()

    const ledger = new Ledger({ path: join(root, "memory.sqlite3") })
    try {
      assert.equal(ledger.assertions().length, 1)
      assert.equal(ledger.assertions()[0]?.model, "fixture/model")
    } finally {
      ledger.close()
    }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test("dispose leaves idle consolidation queued for the next prompt", async () => {
  const root = mkdtempSync(join(tmpdir(), "memory-plugin-"))
  let runnerCalls = 0
  const runner: ConsolidationRunner = {
    async run(input) {
      runnerCalls++
      return {
        model: "fixture/model",
        output: JSON.stringify({
          assertions: [{
            scope: "project",
            category: "decision",
            content: "An in-flight consolidation completed before disposal.",
            confidence: 0.9,
            sourceEventIDs: input.events.map((event) => event.id),
          }],
        }),
      }
    },
  }
  const hooks = await createLocalMemoryPlugin({ consolidationRunner: runner })(context(), {
    capture: true,
    consolidation: true,
    dataDir: root,
  })

  try {
    await hooks.event?.({ event: { type: "session.created", properties: { sessionID: "ses-user" } } } as never)
    await hooks.event?.({ event: { type: "session.idle", properties: { sessionID: "ses-user" } } } as never)
    await hooks.dispose?.()

    const ledger = new Ledger({ path: join(root, "memory.sqlite3") })
    try {
      assert.equal(runnerCalls, 0)
      assert.equal(ledger.status().queuedConsolidations, 1)
      assert.equal(ledger.assertions().length, 0)
    } finally {
      ledger.close()
    }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

import assert from "node:assert/strict"
import { existsSync } from "node:fs"
import { test } from "node:test"

import { getDataLayout, parseConfig, SAFE_DEFAULTS } from "../src/config.js"

test("all subsystems default off", () => {
  const config = parseConfig({}, { LOCALAPPDATA: "C:\\Users\\test\\AppData\\Local" }, "win32")

  assert.deepEqual(
    { capture: config.capture, consolidation: config.consolidation, injection: config.injection },
    SAFE_DEFAULTS,
  )
  assert.equal(config.issues.length, 0)
})

test("subsystem switches are independent", () => {
  const config = parseConfig({ capture: true, consolidation: false, injection: true })

  assert.equal(config.capture, true)
  assert.equal(config.consolidation, false)
  assert.equal(config.injection, true)
})

test("explicit environment switches and data directory enable isolated deployment", () => {
  const config = parseConfig({}, {
    AXEL_OPENCODE_MEMORY_CAPTURE: "true",
    AXEL_OPENCODE_MEMORY_CONSOLIDATION: "false",
    AXEL_OPENCODE_MEMORY_INJECTION: "true",
    AXEL_OPENCODE_MEMORY_DATA_DIR: "C:\\Temp\\axel-memory",
  }, "win32")

  assert.deepEqual(
    { capture: config.capture, consolidation: config.consolidation, injection: config.injection },
    { capture: true, consolidation: false, injection: true },
  )
  assert.equal(config.dataDir, "C:\\Temp\\axel-memory")
  assert.equal(config.issues.length, 0)
})

test("invalid options fail closed", () => {
  const config = parseConfig({ capture: "yes", consolidation: 1, dataDir: "relative" })

  assert.equal(config.capture, false)
  assert.equal(config.consolidation, false)
  assert.equal(config.issues.length, 3)
})

test("data layout resolution has no file-system side effects", () => {
  const root = new URL(`./does-not-exist-${Date.now()}`, import.meta.url).pathname
  assert.equal(existsSync(root), false)

  const layout = getDataLayout(root)

  assert.equal(existsSync(layout.root), false)
  assert.match(layout.database, /memory\.sqlite3$/)
})

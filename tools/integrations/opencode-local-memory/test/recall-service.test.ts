import assert from "node:assert/strict"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

import { Ledger } from "../src/capture/ledger.js"
import { ProjectRegistry } from "../src/projects/registry.js"
import { RecallService } from "../src/recall/service.js"

test("recall service applies one policy to local and optional sources", async () => {
  const root = mkdtempSync(join(tmpdir(), "memory-recall-service-"))
  const ledger = new Ledger({ path: join(root, "memory.sqlite3") })
  try {
    const service = new RecallService(ledger, new ProjectRegistry(ledger), {
      sources: [{
        name: "fixture-source",
        async search() {
          return [{
            handle: "fixture:1",
            source: "fixture-source",
            scope: "personal",
            content: "The unified policy is active.",
            confidence: 0.95,
            status: "current",
          }]
        },
      }],
    })

    const context = await service.context(root)

    assert.match(context, /Unified Memory Context/)
    assert.match(context, /\[fixture:1\]/)
  } finally {
    ledger.close()
    rmSync(root, { recursive: true, force: true })
  }
})

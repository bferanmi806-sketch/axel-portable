import { tool } from "@opencode-ai/plugin"

import type { MemoryControlService } from "./service.js"
import { createBackup } from "../operations.js"
import type { Ledger } from "../capture/ledger.js"

export function createMemoryControlTools(controls: MemoryControlService, ledger: Ledger, backupDirectory: string) {
  return {
    memory_inspect: tool({
      description: "Inspect a local memory assertion and its evidence chain.",
      args: { assertion_id: tool.schema.string() },
      async execute(args) { return JSON.stringify(controls.inspect(args.assertion_id) ?? null, null, 2) },
    }),
    memory_correct: tool({
      description: "Create a user-authored correction that supersedes a current assertion.",
      args: { assertion_id: tool.schema.string(), content: tool.schema.string() },
      async execute(args) { return JSON.stringify(await controls.correct(args.assertion_id, args.content), null, 2) },
    }),
    memory_forget: tool({
      description: "Preview or confirm permanent deletion of source events and affected derived assertions.",
      args: { event_ids: tool.schema.array(tool.schema.string()), confirmed: tool.schema.boolean() },
      async execute(args) {
        return JSON.stringify(args.confirmed
          ? await controls.forget(args.event_ids, true)
          : controls.previewForget(args.event_ids), null, 2)
      },
    }),
    memory_rebuild: tool({
      description: "Delete derived assertions and cursors so retained events can be consolidated again.",
      args: {},
      async execute() { await controls.rebuild(); return "Derived memory cleared. Re-run consolidation to rebuild it." },
    }),
    memory_export: tool({
      description: "Export non-authoritative local memory assertions as Markdown.",
      args: {},
      async execute() { return controls.exportMarkdown() },
    }),
    memory_status: tool({
      description: "Show privacy-safe local memory health and queue status.",
      args: {},
      async execute() { return JSON.stringify(ledger.status(), null, 2) },
    }),
    memory_backup: tool({
      description: "Create a consistent local memory database backup and manifest.",
      args: {},
      async execute() { return JSON.stringify(await createBackup(ledger, backupDirectory), null, 2) },
    }),
  }
}

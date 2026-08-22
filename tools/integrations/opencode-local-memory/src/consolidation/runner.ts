import { randomUUID } from "node:crypto"

import type { PluginInput } from "@opencode-ai/plugin"

import { INTERNAL_SESSION_PREFIX, InternalSessionRegistry } from "../internal-session.js"
import { buildConsolidationPrompt } from "./schema.js"
import type { ConsolidationInput, ConsolidationRunner } from "./types.js"

const DEFAULT_TIMEOUT_MS = 30_000

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_, reject) => {
        timer = setTimeout(() => reject(new Error("internal consolidation timed out")), timeoutMs)
      }),
    ])
  } finally {
    if (timer !== undefined) clearTimeout(timer)
  }
}

export function createOpenCodeRunner(
  client: PluginInput["client"],
  directory: string,
  internalSessions: InternalSessionRegistry,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): ConsolidationRunner {
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1) throw new Error("timeoutMs must be a positive safe integer")
  return {
    async run(input) {
      const availableTools = await client.tool.ids({ query: { directory } })
      if (availableTools.error || !availableTools.data) throw new Error("could not determine internal session tools")
      const disabledTools = Object.fromEntries(availableTools.data.map((id) => [id, false]))
      const created = await client.session.create({
        body: { title: `${INTERNAL_SESSION_PREFIX}${randomUUID()}` },
        query: { directory },
      })
      if (created.error || !created.data) throw new Error("could not create internal consolidation session")
      internalSessions.mark(created.data.id)

      const response = await withTimeout(client.session.prompt({
        path: { id: created.data.id },
        query: { directory },
        body: {
          system: "You are an internal memory extraction worker. Return only the requested JSON. Do not call tools.",
          tools: disabledTools,
          parts: [{ type: "text", text: buildConsolidationPrompt(input) }],
        },
      }), timeoutMs)
      if (response.error || !response.data) throw new Error("internal consolidation session failed")
      const output = response.data.parts
        .map((part) => typeof (part as { text?: unknown }).text === "string" ? (part as { text: string }).text : "")
        .join("\n")
        .trim()
      if (!output) throw new Error("internal consolidation session returned no text")
      return { model: "opencode/active-default", output }
    },
  }
}

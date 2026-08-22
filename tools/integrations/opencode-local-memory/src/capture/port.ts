import type { CapturePort } from "../ports.js"
import { readSessionID } from "../internal-session.js"
import type { ProjectRegistry } from "../projects/registry.js"
import { Ledger } from "./ledger.js"
import { CapturePolicy } from "./policy.js"
import type { CaptureCandidate } from "./types.js"

export function createLedgerCapturePort(
  ledger: Ledger,
  policy: CapturePolicy,
  workspace?: string,
  projects?: ProjectRegistry,
): CapturePort {
  let projectID: Promise<string> | undefined
  const record = async (candidate: CaptureCandidate): Promise<void> => {
    const decision = policy.sanitize(workspace === undefined ? candidate : { ...candidate, workspace })
    if (decision.kind === "accepted") {
      if (workspace && projects) {
        projectID ??= projects.resolve(workspace).then((resolution) => resolution.project.id)
      }
      await ledger.append({
        ...decision.capture,
        projectID: projectID ? await projectID : decision.capture.projectID,
      })
      return
    }
    if (decision.kind === "quarantined") await ledger.quarantine(decision.reason)
  }

  return {
    onEvent: async (input) => {
      const sessionID = readSessionID(input)
      const event = input.event as { type?: unknown; properties?: unknown }
      if (!sessionID || typeof event.type !== "string") {
        await ledger.quarantine("event missing session ID or type")
        return
      }
      await record(candidate({
        sessionID,
        eventType: event.type,
        source: "event",
        payload: event.properties ?? null,
      }))
    },
    onMessages: async (_input, output) => {
      for (const message of output.messages) {
        const sessionID = readSessionID(message)
        const messageID = readMessageID(message)
        if (!sessionID || !messageID) continue
        await record(candidate({
          id: `message:${messageID}`,
          sessionID,
          eventType: "message",
          source: "message",
          payload: { info: message.info, parts: message.parts },
        }))
      }
    },
    onToolBefore: async (input, output) => record(candidate({
      id: `tool.before:${input.callID}`,
      sessionID: input.sessionID,
      eventType: `tool.execute.before:${input.tool}`,
      source: "tool.before",
      tool: input.tool,
      paths: readPaths(output.args),
      payload: output.args,
    })),
    onToolAfter: async (input, output) => record(candidate({
      id: `tool.after:${input.callID}`,
      sessionID: input.sessionID,
      eventType: `tool.execute.after:${input.tool}`,
      source: "tool.after",
      tool: input.tool,
      paths: readPaths(input.args),
      payload: { args: input.args, output: output.output, metadata: output.metadata },
    })),
    onCompacting: async (input) => record(candidate({
      id: `compacting:${input.sessionID}`,
      sessionID: input.sessionID,
      eventType: "session.compacting",
      source: "compacting",
      payload: null,
    })),
  }
}

function candidate(value: CaptureCandidate): CaptureCandidate {
  return value
}

function readMessageID(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return undefined
  const info = (value as { info?: unknown }).info
  if (!info || typeof info !== "object") return undefined
  const id = (info as { id?: unknown }).id
  return typeof id === "string" ? id : undefined
}

function readPaths(value: unknown): string[] {
  if (!value || typeof value !== "object") return []
  return Object.entries(value as Record<string, unknown>)
    .filter(([key, candidate]) => /(?:path|file|directory|cwd)$/i.test(key) && typeof candidate === "string")
    .map(([, candidate]) => candidate as string)
}

import type { Hooks, Plugin, PluginInput, PluginOptions } from "@opencode-ai/plugin"

import { getDataLayout, parseConfig } from "./config.js"
import { Ledger } from "./capture/ledger.js"
import { CapturePolicy } from "./capture/policy.js"
import { createLedgerCapturePort } from "./capture/port.js"
import { createOpenCodeRunner } from "./consolidation/runner.js"
import { ConsolidationService } from "./consolidation/service.js"
import type { ConsolidationRunner } from "./consolidation/types.js"
import { InternalSessionRegistry, readSessionID } from "./internal-session.js"
import { ProjectRegistry } from "./projects/registry.js"
import { createProjectListTool } from "./projects/tool.js"
import { RecallService } from "./recall/service.js"
import type { RecallSource } from "./recall/policy.js"
import { MemoryControlService } from "./controls/service.js"
import { createMemoryControlTools } from "./controls/tools.js"
import type { MemoryPorts, PluginLogger } from "./ports.js"
import { readSupportedEventType } from "./supported-events.js"

export type PluginDependencies = {
  ports?: MemoryPorts
  internalSessions?: InternalSessionRegistry
  logger?: PluginLogger
  consolidationRunner?: ConsolidationRunner
  recallSources?: readonly RecallSource[]
}

function createLogger(input: PluginInput): PluginLogger {
  return {
    async warn(message, extra = {}) {
      try {
        await input.client.app.log({
          body: {
            service: "axel-opencode-local-memory",
            level: "warn",
            message,
            extra,
          },
        })
      } catch {
        // Diagnostics must never become a second failure path.
      }
    },
  }
}

async function safeInvoke(
  logger: PluginLogger,
  operation: string,
  invoke: (() => Promise<void>) | undefined,
): Promise<void> {
  if (!invoke) return
  try {
    await invoke()
  } catch (error) {
    await logger.warn("Memory subsystem operation failed", {
      operation,
      error: error instanceof Error ? error.message : "unknown error",
    })
  }
}

function readMessagesSessionID(output: unknown): string | undefined {
  if (!output || typeof output !== "object") return undefined
  const messages = (output as { messages?: unknown }).messages
  if (!Array.isArray(messages)) return undefined

  for (let index = messages.length - 1; index >= 0; index--) {
    const sessionID = readSessionID(messages[index])
    if (sessionID) return sessionID
  }
}

export function createLocalMemoryPlugin(dependencies: PluginDependencies = {}): Plugin {
  return async (input, options: PluginOptions = {}): Promise<Hooks> => {
    const config = parseConfig(options)
    const logger = dependencies.logger ?? createLogger(input)
    const layout = getDataLayout(config.dataDir)
    const ledger = (config.capture && !dependencies.ports?.capture) || (config.consolidation && !dependencies.ports?.consolidation) || (config.injection && !dependencies.ports?.injection)
      ? new Ledger({ path: layout.database })
      : undefined
    const projects = ledger ? new ProjectRegistry(ledger) : undefined
    const controls = ledger ? new MemoryControlService(ledger) : undefined
    const capturePort = dependencies.ports?.capture ?? (ledger ? createLedgerCapturePort(
      ledger,
      new CapturePolicy({
        ...config.capturePolicy,
        allowedReferenceRoot: layout.references,
        }),
        input.directory,
        projects,
      ) : undefined)
    const ports: MemoryPorts = {
      ...dependencies.ports,
      ...(capturePort ? { capture: capturePort } : {}),
    }
    const internalSessions = dependencies.internalSessions ?? new InternalSessionRegistry()
    const recallPort = dependencies.ports?.injection ?? (ledger && projects && config.injection
      ? (() => {
            const recall = new RecallService(ledger, projects, dependencies.recallSources
              ? { sources: dependencies.recallSources }
              : {})
          return {
            onSystem: async (_input: unknown, output: { system: string[] }) => {
              const context = await recall.context(input.directory)
              if (context && !output.system.some((entry) => entry.includes("## Local Memory Context"))) output.system.push(context)
            },
          }
        })()
      : undefined)
    if (recallPort) ports.injection = recallPort
    const pendingOperations = new Set<Promise<void>>()
    const trackOperation = (task: Promise<void>): Promise<void> => {
      pendingOperations.add(task)
      task.then(
        () => pendingOperations.delete(task),
        () => pendingOperations.delete(task),
      )
      return task
    }
    let drainConsolidation: (() => Promise<void>) | undefined
    const consolidationPort = dependencies.ports?.consolidation ?? (ledger && config.consolidation
      ? (() => {
          const runner = dependencies.consolidationRunner ?? createOpenCodeRunner(input.client, input.directory, internalSessions, config.consolidationTimeoutMs)
          const service = new ConsolidationService(ledger, runner, async (error) => logger.warn("Consolidation runner failed", {
            error: error instanceof Error ? error.message.slice(0, 256) : "unknown error",
          }))
          drainConsolidation = () => service.drainOne()
          return {
            onSessionBoundary: async ({ sessionID }: { sessionID: string }) => {
              await service.queue(sessionID)
            },
          }
        })()
      : undefined)
    if (consolidationPort) ports.consolidation = consolidationPort

    for (const issue of config.issues) {
      await logger.warn("Invalid local memory configuration", { issue })
    }

    const isInternal = (value: unknown) => internalSessions.isInternalValue(value)

    return {
      ...(ledger ? { tool: {
        ...(projects ? { memory_project_list: createProjectListTool(projects) } : {}),
        ...(controls ? createMemoryControlTools(controls, ledger, layout.backups) : {}),
      } } : {}),
      dispose: async () => {
        if (!ledger) return
        while (pendingOperations.size > 0) {
          await Promise.allSettled([...pendingOperations])
        }
        await ledger.flush()
        ledger.close()
      },
      event: async (eventInput) => {
        if (isInternal(eventInput)) return
        const eventType = readSupportedEventType(eventInput)
        if (!eventType) {
          await logger.warn("Ignored unsupported or malformed OpenCode event")
          return
        }

        if (config.capture) {
          const hook = ports.capture?.onEvent
          await trackOperation(safeInvoke(logger, "capture.event", hook ? () => hook(eventInput) : undefined))
        }

        if (config.consolidation && eventType === "session.idle") {
          const sessionID = readSessionID(eventInput)
          if (sessionID) {
            const hook = ports.consolidation?.onSessionBoundary
            await trackOperation(safeInvoke(logger, "consolidation.idle", hook ? () =>
              hook({ sessionID, reason: "idle" }) : undefined)
            )
          }
        }
      },

      "tool.execute.before": async (toolInput, output) => {
        if (!config.capture || isInternal(toolInput)) return
        const hook = ports.capture?.onToolBefore
        await trackOperation(safeInvoke(logger, "capture.tool.before", hook ? () => hook(toolInput, output) : undefined))
      },

      "tool.execute.after": async (toolInput, output) => {
        if (!config.capture || isInternal(toolInput)) return
        const hook = ports.capture?.onToolAfter
        await trackOperation(safeInvoke(logger, "capture.tool.after", hook ? () => hook(toolInput, output) : undefined))
      },

      "experimental.chat.messages.transform": async (messageInput, output) => {
        if (!config.capture || isInternal(messageInput) || internalSessions.has(readMessagesSessionID(output))) return
        const hook = ports.capture?.onMessages
        await trackOperation(safeInvoke(logger, "capture.messages", hook ? () => hook(messageInput, output) : undefined))
      },

      "experimental.session.compacting": async (compactingInput, output) => {
        if (isInternal(compactingInput)) return
        if (config.capture) {
          const hook = ports.capture?.onCompacting
          await trackOperation(safeInvoke(logger, "capture.compacting", hook ? () => hook(compactingInput, output) : undefined))
        }
        if (config.consolidation) {
          const hook = ports.consolidation?.onSessionBoundary
          await trackOperation(safeInvoke(logger, "consolidation.compacting", hook ? () =>
            hook({
              sessionID: compactingInput.sessionID,
              reason: "compacting",
            }) : undefined)
          )
        }
      },

      "experimental.chat.system.transform": async (systemInput, output) => {
        if (isInternal(systemInput)) return
        if (config.consolidation) {
          const drain = drainConsolidation
          await trackOperation(safeInvoke(logger, "consolidation.drain", drain
            ? () => drain()
            : undefined))
        }
        if (config.injection) {
          const hook = ports.injection?.onSystem
          await trackOperation(safeInvoke(logger, "injection.system", hook ? () => hook(systemInput, output) : undefined))
        }
      },
    }
  }
}

export const LocalMemoryPlugin = createLocalMemoryPlugin()

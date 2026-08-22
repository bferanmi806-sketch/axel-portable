import type { Hooks } from "@opencode-ai/plugin"

export type CapturePort = {
  onEvent?: NonNullable<Hooks["event"]>
  onMessages?: NonNullable<Hooks["experimental.chat.messages.transform"]>
  onToolBefore?: NonNullable<Hooks["tool.execute.before"]>
  onToolAfter?: NonNullable<Hooks["tool.execute.after"]>
  onCompacting?: NonNullable<Hooks["experimental.session.compacting"]>
}

export type ConsolidationPort = {
  onSessionBoundary?: (input: { sessionID: string; reason: "idle" | "compacting" }) => Promise<void>
}

export type InjectionPort = {
  onSystem?: NonNullable<Hooks["experimental.chat.system.transform"]>
}

export type MemoryPorts = {
  capture?: CapturePort
  consolidation?: ConsolidationPort
  injection?: InjectionPort
}

export type PluginLogger = {
  warn(message: string, extra?: Record<string, unknown>): Promise<void>
}

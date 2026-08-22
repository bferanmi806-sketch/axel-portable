export const SUPPORTED_EVENT_TYPES = Object.freeze([
  "message.part.removed",
  "message.part.updated",
  "message.removed",
  "message.updated",
  "session.compacted",
  "session.created",
  "session.deleted",
  "session.diff",
  "session.error",
  "session.idle",
  "session.status",
  "session.updated",
] as const)

const supportedEventTypes = new Set<string>(SUPPORTED_EVENT_TYPES)

export function readSupportedEventType(input: unknown): string | undefined {
  if (!input || typeof input !== "object") return undefined
  const event = (input as { event?: unknown }).event
  if (!event || typeof event !== "object") return undefined
  const type = (event as { type?: unknown }).type
  return typeof type === "string" && supportedEventTypes.has(type) ? type : undefined
}

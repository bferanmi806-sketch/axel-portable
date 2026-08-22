export const INTERNAL_SESSION_PREFIX = "axel-memory-internal-"

export class InternalSessionRegistry {
  readonly #sessionIDs = new Set<string>()

  mark(sessionID: string): void {
    if (!sessionID) throw new Error("Internal session ID cannot be empty")
    this.#sessionIDs.add(sessionID)
  }

  has(sessionID: string | undefined): boolean {
    return typeof sessionID === "string" &&
      (sessionID.startsWith(INTERNAL_SESSION_PREFIX) || this.#sessionIDs.has(sessionID))
  }

  isInternalValue(value: unknown): boolean {
    return this.has(readSessionID(value)) || hasInternalTitle(value, new WeakSet())
  }

  remove(sessionID: string): void {
    this.#sessionIDs.delete(sessionID)
  }
}

export function readSessionID(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return undefined

  const direct = (value as { sessionID?: unknown }).sessionID
  if (typeof direct === "string") return direct

  const properties = (value as { properties?: unknown }).properties
  if (properties && typeof properties === "object") {
    const nested = (properties as { sessionID?: unknown }).sessionID
    if (typeof nested === "string") return nested
    const info = (properties as { info?: unknown }).info
    if (info && typeof info === "object") {
      const id = (info as { id?: unknown }).id
      if (typeof id === "string") return id
    }
  }

  const info = (value as { info?: unknown }).info
  if (info && typeof info === "object") {
    const nested = (info as { sessionID?: unknown }).sessionID
    if (typeof nested === "string") return nested
    const id = (info as { id?: unknown }).id
    if (typeof id === "string") return id
  }

  const event = (value as { event?: unknown }).event
  return event === value ? undefined : readSessionID(event)
}

function hasInternalTitle(value: unknown, seen: WeakSet<object>): boolean {
  if (!value || typeof value !== "object") return false
  if (seen.has(value)) return false
  seen.add(value)
  const title = (value as { title?: unknown }).title
  if (typeof title === "string" && title.startsWith(INTERNAL_SESSION_PREFIX)) return true
  for (const key of ["info", "properties", "event"] as const) {
    const nested: unknown = (value as Record<string, unknown>)[key]
    if (nested !== value && hasInternalTitle(nested, seen)) return true
  }
  return false
}

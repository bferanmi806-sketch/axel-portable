export type SanitizerAction =
  | "redacted:authorization"
  | "redacted:credential"
  | "redacted:private-key"
  | "redacted:configured-pattern"
  | "bounded:oversized"
  | "bounded:binary"

export type PayloadClass = "text" | "oversized" | "binary"

export type SanitizedCapture = {
  id: string
  sessionID: string
  projectID: string
  eventType: string
  source: "event" | "message" | "tool.before" | "tool.after" | "compacting"
  occurredAt: string
  payloadClass: PayloadClass
  payload: string | null
  payloadHash: string
  byteLength: number
  localReference: string | null
  sanitizerActions: readonly SanitizerAction[]
}

export type CaptureDecision =
  | { kind: "accepted"; capture: SanitizedCapture }
  | { kind: "excluded"; reason: "workspace" | "path" | "tool" }
  | { kind: "quarantined"; reason: string }

export type CaptureCandidate = {
  id?: string
  sessionID: string
  projectID?: string
  eventType: string
  source: SanitizedCapture["source"]
  occurredAt?: string
  workspace?: string
  tool?: string
  paths?: readonly string[]
  payload: unknown
  localReference?: string
}

export type LedgerEvent = SanitizedCapture & {
  sequence: number
}

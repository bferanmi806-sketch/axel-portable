import { createHash } from "node:crypto"
import { lstatSync, realpathSync } from "node:fs"
import { isAbsolute, relative, resolve } from "node:path"

import type { CaptureCandidate, CaptureDecision, SanitizedCapture, SanitizerAction } from "./types.js"

const DEFAULT_MAX_TEXT_BYTES = 64 * 1024
const MAX_DEPTH = 20
const MAX_KEYS = 1000
const SECRET_PATTERNS: ReadonlyArray<{ action: SanitizerAction; pattern: RegExp }> = [
  { action: "redacted:private-key", pattern: /-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----[\s\S]*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----/gi },
  { action: "redacted:authorization", pattern: /\bBearer\s+[A-Za-z0-9._~+\-/=]+/gi },
  { action: "redacted:credential", pattern: /\b(?:sk|sk-proj|sk-ant)-[A-Za-z0-9_-]{12,}\b/g },
  { action: "redacted:credential", pattern: /\b(?:api[_-]?key|token|password|passwd|secret)\s*[:=]\s*[^\s,;"'}]+/gi },
]

export type CapturePolicyOptions = {
  excludedWorkspaces?: readonly string[]
  excludedTools?: readonly string[]
  excludedPaths?: readonly string[]
  contentRedactions?: readonly string[]
  maxTextBytes?: number | undefined
  allowedReferenceRoot?: string
}

export class CapturePolicy {
  readonly #excludedWorkspaces: ReadonlySet<string>
  readonly #excludedTools: ReadonlySet<string>
  readonly #excludedPaths: readonly string[]
  readonly #contentRedactions: readonly string[]
  readonly #maxTextBytes: number
  readonly #allowedReferenceRoot: string | undefined

  constructor(options: CapturePolicyOptions = {}) {
    this.#excludedWorkspaces = new Set(options.excludedWorkspaces ?? [])
    this.#excludedTools = new Set(options.excludedTools ?? [])
    this.#excludedPaths = options.excludedPaths ?? []
    this.#contentRedactions = options.contentRedactions ?? []
    this.#maxTextBytes = options.maxTextBytes ?? DEFAULT_MAX_TEXT_BYTES
    this.#allowedReferenceRoot = options.allowedReferenceRoot ? resolve(options.allowedReferenceRoot) : undefined
    if (!Number.isSafeInteger(this.#maxTextBytes) || this.#maxTextBytes < 1) {
      throw new Error("maxTextBytes must be a positive safe integer")
    }
  }

  sanitize(candidate: CaptureCandidate): CaptureDecision {
    if (!candidate.sessionID || !candidate.eventType) {
      return { kind: "quarantined", reason: "missing capture identity" }
    }
    if (candidate.workspace && this.#excludedWorkspaces.has(candidate.workspace)) {
      return { kind: "excluded", reason: "workspace" }
    }
    if (candidate.tool && this.#excludedTools.has(candidate.tool)) {
      return { kind: "excluded", reason: "tool" }
    }
    if (candidate.paths?.some((path) => this.#matchesExcludedPath(path))) {
      return { kind: "excluded", reason: "path" }
    }

    try {
      const serialized = serializePayload(candidate.payload)
      const actions: SanitizerAction[] = []
      const localReference = this.#validateReference(candidate.localReference)
      if (serialized.kind === "binary") {
        actions.push("bounded:binary")
        return {
          kind: "accepted",
          capture: this.#capture(candidate, null, serialized.hash, serialized.byteLength, "binary", localReference, actions),
        }
      }

      const payload = redact(serialized.value, actions, this.#contentRedactions)
      const byteLength = Buffer.byteLength(payload, "utf8")
      if (byteLength > this.#maxTextBytes) {
        actions.push("bounded:oversized")
        return {
          kind: "accepted",
          capture: this.#capture(candidate, null, hash(payload), byteLength, "oversized", localReference, actions),
        }
      }

      return {
        kind: "accepted",
        capture: this.#capture(candidate, payload, hash(payload), byteLength, "text", localReference, actions),
      }
    } catch {
      return { kind: "quarantined", reason: "payload normalization failed" }
    }
  }

  #capture(
    candidate: CaptureCandidate,
    payload: string | null,
    payloadHash: string,
    byteLength: number,
    payloadClass: SanitizedCapture["payloadClass"],
    localReference: string | null,
    sanitizerActions: readonly SanitizerAction[],
  ): SanitizedCapture {
    return {
      id: candidate.id ?? stableID(candidate, payloadHash),
      sessionID: candidate.sessionID,
      projectID: candidate.projectID ?? "unscoped",
      eventType: candidate.eventType,
      source: candidate.source,
      occurredAt: candidate.occurredAt ?? new Date().toISOString(),
      payloadClass,
      payload,
      payloadHash,
      byteLength,
      localReference,
      sanitizerActions,
    }
  }

  #matchesExcludedPath(path: string): boolean {
    const normalized = normalizePath(path)
    return this.#excludedPaths.some((pattern) => {
      const excluded = normalizePath(pattern)
      return normalized === excluded || normalized.startsWith(`${excluded}/`)
    })
  }

  #validateReference(candidate: string | undefined): string | null {
    if (!candidate) return null
    if (!this.#allowedReferenceRoot || !isAbsolute(candidate)) return null

    const root = realpathSync(this.#allowedReferenceRoot)
    const target = realpathSync(candidate)
    const pathFromRoot = relative(root, target)
    if (pathFromRoot === "" || pathFromRoot.startsWith("..") || isAbsolute(pathFromRoot)) return null
    if (!lstatSync(target).isFile()) return null
    return target
  }
}

function serializePayload(value: unknown): { kind: "text"; value: string } | { kind: "binary"; hash: string; byteLength: number } {
  if (Buffer.isBuffer(value) || value instanceof Uint8Array) {
    const buffer = Buffer.from(value)
    return { kind: "binary", hash: hash(buffer), byteLength: buffer.byteLength }
  }

  return { kind: "text", value: JSON.stringify(normalizeValue(value, 0, { count: 0 })) }
}

function normalizeValue(value: unknown, depth: number, state: { count: number }): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value
  if (typeof value === "number") return Number.isFinite(value) ? value : String(value)
  if (typeof value === "bigint") return value.toString()
  if (typeof value === "undefined") return null
  if (depth >= MAX_DEPTH || state.count >= MAX_KEYS) return "[TRUNCATED]"
  if (Buffer.isBuffer(value) || value instanceof Uint8Array) return "[BINARY]"
  if (Array.isArray(value)) return value.map((item) => normalizeValue(item, depth + 1, state))
  if (typeof value !== "object") return String(value)

  const normalized: Record<string, unknown> = {}
  for (const key of Object.keys(value as object)) {
    state.count += 1
    if (state.count > MAX_KEYS) break
    normalized[key] = normalizeValue((value as Record<string, unknown>)[key], depth + 1, state)
  }
  return normalized
}

function redact(input: string, actions: SanitizerAction[], configuredPatterns: readonly string[]): string {
  let output = input
  output = output.replace(/"(?:authorization|x-api-key|api[_-]?key|token|password|passwd|secret)"\s*:\s*"(?:\\.|[^"\\])*"/gi, (match) => {
    actions.push("redacted:authorization")
    return `${match.slice(0, match.indexOf(":"))}:"[REDACTED]"`
  })
  for (const { action, pattern } of SECRET_PATTERNS) {
    output = output.replace(pattern, () => {
      actions.push(action)
      return "[REDACTED]"
    })
  }
  for (const configured of configuredPatterns) {
    if (!configured) continue
    output = output.split(configured).join("[REDACTED]")
    if (input.includes(configured)) actions.push("redacted:configured-pattern")
  }
  return output
}

function hash(value: string | Buffer): string {
  return createHash("sha256").update(value).digest("hex")
}

function stableID(candidate: CaptureCandidate, payloadHash: string): string {
  return createHash("sha256")
    .update(JSON.stringify([candidate.sessionID, candidate.eventType, candidate.source, payloadHash]))
    .digest("hex")
}

function normalizePath(path: string): string {
  return resolve(path).replaceAll("\\", "/").replace(/\/+$/, "").toLocaleLowerCase()
}

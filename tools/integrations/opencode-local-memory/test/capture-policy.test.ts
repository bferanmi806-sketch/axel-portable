import assert from "node:assert/strict"
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

import { CapturePolicy } from "../src/capture/policy.js"

function candidate(payload: unknown, overrides: Record<string, unknown> = {}) {
  return {
    id: "evt-1",
    sessionID: "ses-1",
    eventType: "fixture",
    source: "event" as const,
    payload,
    ...overrides,
  }
}

test("redacts credentials before producing a persisted payload or hash", () => {
  const decision = new CapturePolicy({ contentRedactions: ["customer-secret"] }).sanitize(candidate({
    authorization: "Bearer abcdefghijklmnopqrstuvwxyz",
    password: "password=top-secret",
    privateKey: "-----BEGIN PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----",
    custom: "customer-secret",
  }))

  assert.equal(decision.kind, "accepted")
  if (decision.kind !== "accepted") return
  assert.doesNotMatch(decision.capture.payload ?? "", /top-secret|customer-secret|BEGIN PRIVATE KEY|abcdefghijklmnopqrstuvwxyz/)
  assert.match(decision.capture.payload ?? "", /\[REDACTED\]/)
  assert.ok(decision.capture.sanitizerActions.includes("redacted:authorization"))
  assert.ok(decision.capture.sanitizerActions.includes("redacted:configured-pattern"))
})

test("fallback IDs are derived from sanitized content rather than raw secrets", () => {
  const policy = new CapturePolicy()
  const first = policy.sanitize(candidate("Bearer first-secret-token", { id: undefined }))
  const second = policy.sanitize(candidate("Bearer second-secret-token", { id: undefined }))

  assert.equal(first.kind, "accepted")
  assert.equal(second.kind, "accepted")
  if (first.kind === "accepted" && second.kind === "accepted") {
    assert.equal(first.capture.id, second.capture.id)
    assert.doesNotMatch(first.capture.id, /first-secret-token|second-secret-token/)
  }
})

test("excludes configured workspaces, tools, and paths before serialization", () => {
  const policy = new CapturePolicy({
    excludedWorkspaces: ["C:\\secret-workspace"],
    excludedTools: ["read-secret"],
    excludedPaths: ["C:\\secrets"],
  })

  assert.deepEqual(policy.sanitize(candidate("cannot persist", { workspace: "C:\\secret-workspace" })), {
    kind: "excluded",
    reason: "workspace",
  })
  assert.deepEqual(policy.sanitize(candidate("cannot persist", { tool: "read-secret" })), {
    kind: "excluded",
    reason: "tool",
  })
  assert.deepEqual(policy.sanitize(candidate("cannot persist", { paths: ["C:\\secrets\\key.pem"] })), {
    kind: "excluded",
    reason: "path",
  })
  assert.equal(policy.sanitize(candidate("allowed", { paths: ["C:\\secrets-archive\\note.txt"] })).kind, "accepted")
})

test("oversized and binary content has no persisted body", () => {
  const policy = new CapturePolicy({ maxTextBytes: 12 })
  const oversized = policy.sanitize(candidate("this body is too large"))
  const binary = policy.sanitize(candidate(Buffer.from([0, 1, 2, 3])))

  assert.equal(oversized.kind, "accepted")
  assert.equal(binary.kind, "accepted")
  if (oversized.kind === "accepted") {
    assert.equal(oversized.capture.payloadClass, "oversized")
    assert.equal(oversized.capture.payload, null)
    assert.ok(oversized.capture.sanitizerActions.includes("bounded:oversized"))
  }
  if (binary.kind === "accepted") {
    assert.equal(binary.capture.payloadClass, "binary")
    assert.equal(binary.capture.payload, null)
    assert.ok(binary.capture.sanitizerActions.includes("bounded:binary"))
  }
})

test("only existing regular files within the allowed reference root are retained", () => {
  const root = mkdtempSync(join(tmpdir(), "memory-policy-"))
  const references = join(root, "references")
  const allowedFile = join(references, "output.txt")
  mkdirSync(references)
  writeFileSync(allowedFile, "fixture")

  try {
    const policy = new CapturePolicy({ allowedReferenceRoot: references })
    const allowed = policy.sanitize(candidate("fixture", { localReference: allowedFile }))
    const rejected = policy.sanitize(candidate("fixture", { id: "evt-2", localReference: root }))

    assert.equal(allowed.kind, "accepted")
    assert.equal(rejected.kind, "accepted")
    if (allowed.kind === "accepted") assert.equal(allowed.capture.localReference, allowedFile)
    if (rejected.kind === "accepted") assert.equal(rejected.capture.localReference, null)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

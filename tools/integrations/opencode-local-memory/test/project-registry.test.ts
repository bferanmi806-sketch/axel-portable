import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { mkdtempSync, mkdirSync, renameSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

import { Ledger } from "../src/capture/ledger.js"
import { normalizePath } from "../src/projects/identity.js"
import { ProjectRegistry } from "../src/projects/registry.js"

function git(directory: string, args: readonly string[]): void {
  execFileSync("git", ["-C", directory, ...args], { stdio: "ignore", windowsHide: true })
}

function repository(root: string, name: string, remote: string): string {
  const directory = join(root, name)
  mkdirSync(directory)
  git(directory, ["init"])
  git(directory, ["config", "user.email", "memory@example.test"])
  git(directory, ["config", "user.name", "Memory Test"])
  writeFileSync(join(directory, "README.md"), name)
  git(directory, ["add", "README.md"])
  git(directory, ["commit", "-m", "initial"])
  git(directory, ["remote", "add", "origin", remote])
  return directory
}

function createRegistry(): { ledger: Ledger; registry: ProjectRegistry; root: string } {
  const root = mkdtempSync(join(tmpdir(), "memory-projects-"))
  const ledger = new Ledger({ path: join(root, "memory.sqlite3") })
  return { ledger, registry: new ProjectRegistry(ledger), root }
}

test("repositories and non-repository workspaces resolve to separate stable projects", async () => {
  const { ledger, registry, root } = createRegistry()
  const firstRepository = repository(root, "first", "https://example.test/org/first.git")
  const secondRepository = repository(root, "second", "https://example.test/org/second.git")
  const plainDirectory = join(root, "plain")
  mkdirSync(plainDirectory)

  try {
    const first = await registry.resolve(firstRepository)
    const second = await registry.resolve(secondRepository)
    const plain = await registry.resolve(plainDirectory)
    const repeated = await registry.resolve(firstRepository)

    assert.notEqual(first.project.id, second.project.id)
    assert.notEqual(first.project.id, plain.project.id)
    assert.notEqual(second.project.id, plain.project.id)
    assert.equal(repeated.project.id, first.project.id)
    assert.equal(repeated.evidence, "exact-path")
    assert.equal(registry.inspect().filter((project) => project.kind === "project").length, 3)
  } finally {
    ledger.close()
    rmSync(root, { recursive: true, force: true })
  }
})

test("a linked Git worktree resolves to the existing project by common directory", async () => {
  const { ledger, registry, root } = createRegistry()
  const primary = repository(root, "primary", "https://example.test/org/worktree.git")
  const worktree = join(root, "linked-worktree")

  try {
    const original = await registry.resolve(primary)
    git(primary, ["worktree", "add", "--detach", worktree])
    const linked = await registry.resolve(worktree)

    assert.equal(linked.project.id, original.project.id)
    assert.equal(linked.evidence, "common-git-directory")
    assert.ok(linked.project.paths.includes(normalizePath(worktree)))
  } finally {
    ledger.close()
    rmSync(root, { recursive: true, force: true })
  }
})

test("matching remotes are previewed but clones are never merged automatically", async () => {
  const { ledger, registry, root } = createRegistry()
  const first = repository(root, "first-clone", "https://example.test/org/shared.git")
  const second = repository(root, "second-clone", "https://example.test/org/shared.git")

  try {
    const original = await registry.resolve(first)
    const clone = await registry.resolve(second)

    assert.notEqual(clone.project.id, original.project.id)
    assert.equal(clone.evidence, "new-project")
    assert.deepEqual(clone.remoteCandidates.map((project) => project.id), [original.project.id])
  } finally {
    ledger.close()
    rmSync(root, { recursive: true, force: true })
  }
})

test("path move reconciliation is previewed and preserves the selected project history", async () => {
  const { ledger, registry, root } = createRegistry()
  const originalPath = repository(root, "original", "https://example.test/org/moved.git")
  const movedPath = join(root, "moved")

  try {
    const original = await registry.resolve(originalPath)
    await ledger.append({
      id: "history-event",
      sessionID: "ses-history",
      projectID: original.project.id,
      eventType: "message",
      source: "message",
      occurredAt: "2026-07-26T00:00:00.000Z",
      payloadClass: "text",
      payload: "sanitized",
      payloadHash: "hash",
      byteLength: 9,
      localReference: null,
      sanitizerActions: [],
    })
    renameSync(originalPath, movedPath)

    const preview = registry.previewReconciliation(movedPath)
    assert.deepEqual(preview.candidates.map((project) => project.id), [original.project.id])
    const reconciled = await registry.reconcile(original.project.id, movedPath)
    const resolved = await registry.resolve(movedPath)

    assert.equal(reconciled.id, original.project.id)
    assert.equal(resolved.project.id, original.project.id)
    assert.ok(reconciled.paths.includes(normalizePath(originalPath)))
    assert.ok(reconciled.paths.includes(normalizePath(movedPath)))
    assert.equal(ledger.events("ses-history")[0]?.projectID, original.project.id)
  } finally {
    ledger.close()
    rmSync(root, { recursive: true, force: true })
  }
})

test("normalized path identity is case-insensitive and separator-stable", () => {
  assert.equal(normalizePath("C:\\Projects\\Memory\\"), normalizePath("c:/projects/memory"))
})

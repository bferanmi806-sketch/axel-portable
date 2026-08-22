import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

import { BasicMemoryMarkdownSource, CodememCliSource } from "../src/recall/sources.js"

test("Basic Memory markdown source reads personal notes and query-matched project notes", async () => {
  const root = mkdtempSync(join(tmpdir(), "basic-memory-source-"))
  try {
    const profile = join(root, "profile")
    const currentWork = join(root, "current-work")
    const preferences = join(root, "preferences")
    mkdirSync(profile)
    mkdirSync(currentWork)
    mkdirSync(preferences)
    writeFileSync(join(profile, "personal.md"), "---\ntype: person\n---\n# Profile\nPrefers concise answers.")
    writeFileSync(join(preferences, "style.md"), "# Style\nUses direct technical explanations.")
    writeFileSync(join(currentWork, "project.md"), "# Project\nThe project uses SQLite.")

    const source = new BasicMemoryMarkdownSource({ root })
    const personal = await source.search({ projectID: "project-a" })
    const project = await source.search({ query: "SQLite", projectID: "project-a" })

    assert.deepEqual(personal.map((item) => item.handle), ["basic-memory:preferences/style.md", "basic-memory:profile/personal.md"])
    assert.deepEqual(project.map((item) => item.handle), [
      "basic-memory:current-work/project.md",
      "basic-memory:preferences/style.md",
      "basic-memory:profile/personal.md",
    ])
    assert.equal(project.find((item) => item.scope === "project")?.projectID, "project-a")
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test("Codemem CLI source parses JSON without shell interpolation", async () => {
  const output = JSON.stringify([{
    id: 42,
    project: "Axel",
    body_text: "A remembered decision.",
    confidence: 0.95,
    created_at: "2026-07-28T00:00:00.000Z",
  }])
  const source = new CodememCliSource({
    command: [process.execPath, "-e", `process.stdout.write(${JSON.stringify(output)})`],
    allProjects: false,
  })

  const [record] = await source.search({ query: "a query; no shell", projectID: "project-a", projectPath: "C:\\workspace" })

  assert.equal(record?.handle, "codemem:42")
  assert.equal(record?.scope, "personal")
  assert.equal(record?.content, "A remembered decision.")
})

test("all-project Codemem results require an explicit project mapping", async () => {
  const output = JSON.stringify([
    { id: 1, project: "other-project", body_text: "Other project detail.", confidence: 0.95 },
    { id: 2, project: "current-project", body_text: "Current project detail.", confidence: 0.95 },
    { id: 3, project: "Axel", body_text: "Personal detail.", confidence: 0.95 },
  ])
  const source = new CodememCliSource({
    command: [process.execPath, "-e", `process.stdout.write(${JSON.stringify(output)})`],
    allProjects: true,
    projectIDs: { "current-project": "project-a" },
  })

  const records = await source.search({ projectID: "project-a", query: "details" })
  assert.deepEqual(records.map((record) => record.handle), ["codemem:2", "codemem:3"])
})

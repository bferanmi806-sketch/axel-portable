import { chmodSync, mkdirSync, statSync } from "node:fs"
import { dirname } from "node:path"
import { randomUUID } from "node:crypto"

import type { AssertionProposal, AssertionRecord, ConsolidationInput } from "../consolidation/types.js"
import type { ProjectRecord, WorkspaceIdentity } from "../projects/types.js"
import type { LedgerEvent, SanitizedCapture } from "./types.js"
import { openSQLite } from "./sqlite.js"
import type { SQLiteDatabase } from "./sqlite.js"

const SCHEMA_VERSION = 4
const activeLedgerOwners = new Set<string>()

export type LedgerOptions = {
  path: string
  busyTimeoutMs?: number
}

export type LedgerStatus = {
  schemaVersion: number
  integrity: string
  events: number
  assertions: number
  quarantines: number
  pendingRetries: number
  queuedConsolidations: number
  diskBytes: number
}

type EventRow = {
  id: string
  project_id: string
  session_id: string
  sequence: number
  event_type: string
  source: LedgerEvent["source"]
  occurred_at: string
  payload_class: LedgerEvent["payloadClass"]
  payload: string | null
  payload_hash: string
  byte_length: number
  local_reference: string | null
  sanitizer_actions: string
}

type ProjectRow = {
  id: string
  kind: ProjectRecord["kind"]
  status: ProjectRecord["status"]
  repository_common_directory: string | null
  repository_remote: string | null
  created_at: string
  updated_at: string
}

type AssertionRow = {
  id: string
  project_id: string
  scope: AssertionRecord["scope"]
  category: AssertionRecord["category"]
  content: string
  confidence: number
  status: AssertionRecord["status"]
  model: string
  created_at: string
  run_id: string
  supersedes_id: string | null
  source_event_ids: string
}

export type ConsolidationWork = {
  runID: string
  input: ConsolidationInput
}

export type ConsolidationRun = {
  id: string
  sessionID: string
  projectID: string
  startSequence: number
  endSequence: number
  status: "running" | "succeeded" | "failed" | "exhausted"
  attempts: number
  nextRetryAt: string | null
  model: string | null
  errorReason: string | null
}

export class Ledger {
  readonly #db: SQLiteDatabase
  readonly #backup: (destination: string) => Promise<void>
  readonly #ownerID = `${process.pid}:${randomUUID()}`
  #writeTail: Promise<void> = Promise.resolve()

  constructor(options: LedgerOptions) {
    const busyTimeoutMs = options.busyTimeoutMs ?? 5000
    if (!Number.isSafeInteger(busyTimeoutMs) || busyTimeoutMs < 1) {
      throw new Error("busyTimeoutMs must be a positive safe integer")
    }
    mkdirSync(dirname(options.path), { recursive: true, mode: 0o700 })
    const sqlite = openSQLite(options.path)
    this.#db = sqlite.database
    this.#backup = sqlite.backup
    chmodSync(options.path, 0o600)
    this.#db.exec("PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL;")
    this.#db.exec(`PRAGMA busy_timeout = ${busyTimeoutMs};`)
    this.#migrate()
    activeLedgerOwners.add(this.#ownerID)
    this.#recoverInterruptedConsolidations()
  }

  append(capture: SanitizedCapture): Promise<{ inserted: boolean; sequence: number }> {
    return this.#enqueue(() => this.#append(capture))
  }

  quarantine(reason: string): Promise<void> {
    return this.#enqueue(() => {
      this.#db.prepare("INSERT INTO diagnostics (reason, created_at) VALUES (?, ?)").run(reason, new Date().toISOString())
    })
  }

  async createProject(input: { id: string; workspace: WorkspaceIdentity }): Promise<ProjectRecord> {
    return this.#enqueue(() => {
      const now = new Date().toISOString()
      this.#db.prepare(
        `INSERT INTO projects (
          id, workspace, kind, status, repository_common_directory, repository_remote, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      ).run(
        input.id,
        input.workspace.workspacePath,
        "project",
        "active",
        input.workspace.repositoryCommonDirectory,
        input.workspace.repositoryRemote,
        now,
        now,
      )
      this.#db.prepare("INSERT INTO project_paths (project_id, path, created_at) VALUES (?, ?, ?)")
        .run(input.id, input.workspace.workspacePath, now)
      return this.findProject(input.id) as ProjectRecord
    })
  }

  addProjectPath(projectID: string, path: string): Promise<void> {
    return this.#enqueue(() => {
      const now = new Date().toISOString()
      this.#db.prepare("INSERT OR IGNORE INTO project_paths (project_id, path, created_at) VALUES (?, ?, ?)")
        .run(projectID, path, now)
      this.#db.prepare("UPDATE projects SET updated_at = ? WHERE id = ?").run(now, projectID)
    })
  }

  updateProjectIdentity(projectID: string, workspace: WorkspaceIdentity): Promise<void> {
    return this.#enqueue(() => {
      this.#db.prepare(
        `UPDATE projects
         SET workspace = ?, repository_common_directory = ?, repository_remote = ?, updated_at = ?
         WHERE id = ?`,
      ).run(
        workspace.workspacePath,
        workspace.repositoryCommonDirectory,
        workspace.repositoryRemote,
        new Date().toISOString(),
        projectID,
      )
    })
  }

  findProject(projectID: string): ProjectRecord | undefined {
    const row = this.#db.prepare("SELECT * FROM projects WHERE id = ?").get(projectID) as ProjectRow | undefined
    return row ? this.#project(row) : undefined
  }

  findProjectByPath(path: string): ProjectRecord | undefined {
    const row = this.#db.prepare(
      `SELECT projects.* FROM projects
       JOIN project_paths ON project_paths.project_id = projects.id
       WHERE project_paths.path = ?`,
    ).get(path) as ProjectRow | undefined
    return row ? this.#project(row) : undefined
  }

  findProjectByCommonDirectory(commonDirectory: string): ProjectRecord | undefined {
    const row = this.#db.prepare(
      "SELECT * FROM projects WHERE repository_common_directory = ? AND kind = 'project'",
    ).get(commonDirectory) as ProjectRow | undefined
    return row ? this.#project(row) : undefined
  }

  findProjectsByRemote(remote: string): readonly ProjectRecord[] {
    const rows = this.#db.prepare(
      "SELECT * FROM projects WHERE repository_remote = ? AND kind = 'project' ORDER BY created_at",
    ).all(remote) as ProjectRow[]
    return rows.map((row) => this.#project(row))
  }

  projects(): readonly ProjectRecord[] {
    const rows = this.#db.prepare("SELECT * FROM projects ORDER BY created_at").all() as ProjectRow[]
    return rows.map((row) => this.#project(row))
  }

  assertions(projectID?: string): readonly AssertionRecord[] {
    const rows = projectID
      ? this.#db.prepare("SELECT * FROM assertions WHERE project_id = ? ORDER BY created_at").all(projectID)
      : this.#db.prepare("SELECT * FROM assertions ORDER BY created_at").all()
    return (rows as AssertionRow[]).map((row) => this.#assertion(row))
  }

  inspectAssertion(assertionID: string): { assertion: AssertionRecord; events: readonly LedgerEvent[]; chain: readonly AssertionRecord[] } | undefined {
    const assertion = this.assertions().find((item) => item.id === assertionID)
    if (!assertion) return undefined
    const events = assertion.sourceEventIDs.flatMap((id) => this.events().filter((event) => event.id === id))
    const chain = this.assertions(assertion.projectID).filter((item) => item.id === assertion.id || item.supersedesID === assertion.id || assertion.supersedesID === item.id)
    return { assertion, events, chain }
  }

  correctAssertion(assertionID: string, content: string): Promise<AssertionRecord> {
    return this.#enqueue(() => {
      const prior = this.assertions().find((item) => item.id === assertionID && item.status === "current")
      if (!prior || !content.trim() || content.length > 2_000) throw new Error("Correction target or content is invalid")
      const now = new Date().toISOString()
      const sessionID = `manual:${prior.projectID}`
      const runID = randomUUID()
      this.#db.exec("BEGIN IMMEDIATE")
      try {
        this.#db.prepare("INSERT OR IGNORE INTO sessions (id, project_id, created_at) VALUES (?, ?, ?)").run(sessionID, prior.projectID, now)
        this.#db.prepare(
          `INSERT INTO consolidation_runs (id, session_id, project_id, start_sequence, end_sequence, status, attempts, next_retry_at, model, error_reason, created_at, updated_at)
           VALUES (?, ?, ?, 0, 0, 'succeeded', 1, NULL, 'user', NULL, ?, ?)`,
        ).run(runID, sessionID, prior.projectID, now, now)
        this.#db.prepare("UPDATE assertions SET status = 'superseded' WHERE id = ?").run(prior.id)
        const id = randomUUID()
        this.#db.prepare(
          `INSERT INTO assertions (id, project_id, scope, category, content, confidence, status, model, created_at, run_id, supersedes_id, source_event_ids, fingerprint)
           VALUES (?, ?, ?, 'correction', ?, 1, 'current', 'user', ?, ?, ?, ?, ?)`,
        ).run(id, prior.projectID, prior.scope, content.trim(), now, runID, prior.id, JSON.stringify(prior.sourceEventIDs), `user:${id}`)
        for (const eventID of prior.sourceEventIDs) {
          this.#db.prepare("INSERT INTO assertion_evidence (assertion_id, event_id) VALUES (?, ?)").run(id, eventID)
        }
        this.#db.exec("COMMIT")
        return this.assertions(prior.projectID).find((item) => item.id === id) as AssertionRecord
      } catch (error) {
        this.#db.exec("ROLLBACK")
        throw error
      }
    })
  }

  forgetPreview(eventIDs: readonly string[]): { eventIDs: readonly string[]; assertionIDs: readonly string[] } {
    const unique = [...new Set(eventIDs)]
    const assertionIDs = (this.#db.prepare(`SELECT DISTINCT assertion_id FROM assertion_evidence WHERE event_id IN (${unique.map(() => "?").join(",") || "NULL"})`).all(...unique) as Array<{ assertion_id: string }>)
      .map((row) => row.assertion_id)
    return { eventIDs: unique.filter((id) => this.events().some((event) => event.id === id)), assertionIDs }
  }

  forget(eventIDs: readonly string[]): Promise<{ eventIDs: readonly string[]; assertionIDs: readonly string[] }> {
    return this.#enqueue(() => {
      const preview = this.forgetPreview(eventIDs)
      this.#db.exec("BEGIN IMMEDIATE")
      try {
        for (const assertionID of preview.assertionIDs) {
          this.#db.prepare("UPDATE assertions SET supersedes_id = NULL WHERE supersedes_id = ?").run(assertionID)
          this.#db.prepare("DELETE FROM assertion_evidence WHERE assertion_id = ?").run(assertionID)
          this.#db.prepare("DELETE FROM assertions WHERE id = ?").run(assertionID)
        }
        for (const eventID of preview.eventIDs) this.#db.prepare("DELETE FROM events WHERE id = ?").run(eventID)
        this.#db.exec("COMMIT")
        return preview
      } catch (error) {
        this.#db.exec("ROLLBACK")
        throw error
      }
    })
  }

  rebuildDerived(): Promise<void> {
    return this.#enqueue(() => {
      this.#db.exec("BEGIN IMMEDIATE")
      try {
        this.#db.exec("DELETE FROM assertion_evidence; DELETE FROM assertions; DELETE FROM consolidation_cursors; DELETE FROM consolidation_runs; DELETE FROM consolidation_queue;")
        this.#db.exec("COMMIT")
      } catch (error) {
        this.#db.exec("ROLLBACK")
        throw error
      }
    })
  }

  consolidationRuns(): readonly ConsolidationRun[] {
    const rows = this.#db.prepare("SELECT * FROM consolidation_runs ORDER BY created_at").all() as Array<{
      id: string
      session_id: string
      project_id: string
      start_sequence: number
      end_sequence: number
      status: ConsolidationRun["status"]
      attempts: number
      next_retry_at: string | null
      model: string | null
      error_reason: string | null
    }>
    return rows.map((row) => ({
      id: row.id,
      sessionID: row.session_id,
      projectID: row.project_id,
      startSequence: row.start_sequence,
      endSequence: row.end_sequence,
      status: row.status,
      attempts: row.attempts,
      nextRetryAt: row.next_retry_at,
      model: row.model,
      errorReason: row.error_reason,
    }))
  }

  prepareConsolidation(sessionID: string, maxBytes: number): Promise<ConsolidationWork | undefined> {
    return this.#enqueue(() => {
      const session = this.#db.prepare("SELECT project_id FROM sessions WHERE id = ?").get(sessionID) as { project_id: string } | undefined
      if (!session) return undefined
      const cursor = this.#db.prepare("SELECT last_sequence FROM consolidation_cursors WHERE session_id = ?").get(sessionID) as { last_sequence: number } | undefined
      const lastSequence = cursor?.last_sequence ?? 0
      const now = new Date().toISOString()
      const retry = this.#db.prepare(
        `SELECT id, start_sequence, end_sequence, attempts, next_retry_at
         FROM consolidation_runs
         WHERE session_id = ? AND status = 'failed' AND attempts < 3 AND start_sequence > ?
         ORDER BY start_sequence LIMIT 1`,
      ).get(sessionID, lastSequence) as { id: string; start_sequence: number; end_sequence: number; attempts: number; next_retry_at: string | null } | undefined
      if (retry?.next_retry_at && retry.next_retry_at > now) return undefined

      const blocked = this.#db.prepare(
        "SELECT id FROM consolidation_runs WHERE session_id = ? AND status = 'exhausted' AND start_sequence > ? LIMIT 1",
      ).get(sessionID, lastSequence)
      if (blocked) return undefined

      const events = this.events(sessionID)
        .filter((event) => event.sequence > lastSequence && event.payloadClass === "text" && event.payload !== null)
      const selected: Array<ConsolidationInput["events"][number]> = []
      let byteLength = 0
      const selectedEvents = retry
        ? events.filter((event) => event.sequence >= retry.start_sequence && event.sequence <= retry.end_sequence)
        : events
      for (const event of selectedEvents) {
        const length = Buffer.byteLength(event.payload ?? "", "utf8")
        if (selected.length > 0 && byteLength + length > maxBytes) break
        selected.push({
          id: event.id,
          sequence: event.sequence,
          eventType: event.eventType,
          occurredAt: event.occurredAt,
          payload: event.payload as string,
        })
        byteLength += length
      }
      if (selected.length === 0) return undefined

      const startSequence = retry?.start_sequence ?? selected[0]?.sequence as number
      const endSequence = retry?.end_sequence ?? selected.at(-1)?.sequence as number
      const existing = this.#db.prepare(
        "SELECT id, status, attempts, next_retry_at FROM consolidation_runs WHERE session_id = ? AND start_sequence = ? AND end_sequence = ?",
      ).get(sessionID, startSequence, endSequence) as { id: string; status: string; attempts: number; next_retry_at: string | null } | undefined
      if (existing?.status === "succeeded" || existing?.status === "running" || existing?.status === "exhausted" || (existing?.next_retry_at && existing.next_retry_at > now)) {
        return undefined
      }
      const runID = existing?.id ?? randomUUID()
      if (existing) {
        this.#db.prepare("UPDATE consolidation_runs SET status = 'running', attempts = attempts + 1, owner_id = ?, updated_at = ?, error_reason = NULL WHERE id = ?")
          .run(this.#ownerID, now, runID)
      } else {
        this.#db.prepare(
          `INSERT INTO consolidation_runs (
            id, session_id, project_id, start_sequence, end_sequence, status, attempts, next_retry_at, model, error_reason, owner_id, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, 'running', 1, NULL, NULL, NULL, ?, ?, ?)`,
        ).run(runID, sessionID, session.project_id, startSequence, endSequence, this.#ownerID, now, now)
      }
      return { runID, input: { sessionID, projectID: session.project_id, events: selected } }
    })
  }

  completeConsolidation(runID: string, model: string, assertions: readonly AssertionProposal[]): Promise<void> {
    return this.#enqueue(() => {
      const run = this.#db.prepare("SELECT * FROM consolidation_runs WHERE id = ? AND status = 'running' AND owner_id = ?").get(runID, this.#ownerID) as {
        session_id: string
        project_id: string
        end_sequence: number
      } | undefined
      if (!run) throw new Error("consolidation run is not active")
      const now = new Date().toISOString()
      this.#db.exec("BEGIN IMMEDIATE")
      try {
        for (const assertion of assertions) {
          const fingerprint = JSON.stringify([assertion.scope, assertion.category, assertion.content, assertion.sourceEventIDs])
          const duplicate = this.#db.prepare("SELECT id FROM assertions WHERE run_id = ? AND fingerprint = ?").get(runID, fingerprint)
          if (duplicate) continue
          if (assertion.supersedesID) {
            const prior = this.#db.prepare("SELECT id FROM assertions WHERE id = ? AND project_id = ? AND status = 'current'")
              .get(assertion.supersedesID, run.project_id)
            if (!prior) throw new Error("superseded assertion is not current in this project")
            this.#db.prepare("UPDATE assertions SET status = 'superseded' WHERE id = ?").run(assertion.supersedesID)
          }
          const id = randomUUID()
          this.#db.prepare(
            `INSERT INTO assertions (
              id, project_id, scope, category, content, confidence, status, model, created_at, run_id, supersedes_id, source_event_ids, fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, 'current', ?, ?, ?, ?, ?, ?)`,
          ).run(
            id,
            run.project_id,
            assertion.scope,
            assertion.category,
            assertion.content,
            assertion.confidence,
            model,
            now,
            runID,
            assertion.supersedesID ?? null,
            JSON.stringify(assertion.sourceEventIDs),
            fingerprint,
          )
          for (const eventID of assertion.sourceEventIDs) {
            this.#db.prepare("INSERT INTO assertion_evidence (assertion_id, event_id) VALUES (?, ?)").run(id, eventID)
          }
        }
        this.#db.prepare("UPDATE consolidation_runs SET status = 'succeeded', model = ?, updated_at = ?, error_reason = NULL WHERE id = ?")
          .run(model, now, runID)
        this.#db.prepare(
          "INSERT INTO consolidation_cursors (session_id, last_sequence, updated_at) VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET last_sequence = excluded.last_sequence, updated_at = excluded.updated_at",
        ).run(run.session_id, run.end_sequence, now)
        this.#db.exec("COMMIT")
      } catch (error) {
        this.#db.exec("ROLLBACK")
        throw error
      }
    })
  }

  failConsolidation(runID: string, reason: string): Promise<void> {
    return this.#enqueue(() => {
      const run = this.#db.prepare("SELECT attempts, session_id, end_sequence FROM consolidation_runs WHERE id = ? AND status = 'running' AND owner_id = ?").get(runID, this.#ownerID) as { attempts: number; session_id: string; end_sequence: number } | undefined
      if (!run) return
      const delayMilliseconds = Math.min(60_000, 1_000 * 2 ** (run.attempts - 1))
      const now = new Date().toISOString()
      this.#db.exec("BEGIN IMMEDIATE")
      try {
        const exhausted = run.attempts >= 3
        this.#db.prepare(
          `UPDATE consolidation_runs SET status = ?, next_retry_at = ?, error_reason = ?, owner_id = NULL, updated_at = ? WHERE id = ? AND owner_id = ?`,
        ).run(exhausted ? "exhausted" : "failed", exhausted ? null : new Date(Date.now() + delayMilliseconds).toISOString(), reason.slice(0, 200), now, runID, this.#ownerID)
        if (exhausted) {
          this.#db.prepare(
            "INSERT INTO consolidation_cursors (session_id, last_sequence, updated_at) VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET last_sequence = MAX(last_sequence, excluded.last_sequence), updated_at = excluded.updated_at",
          ).run(run.session_id, run.end_sequence, now)
          this.#db.prepare("DELETE FROM consolidation_queue WHERE session_id = ?").run(run.session_id)
        }
        this.#db.exec("COMMIT")
      } catch (error) {
        this.#db.exec("ROLLBACK")
        throw error
      }
    })
  }

  events(sessionID?: string): LedgerEvent[] {
    const rows = sessionID
      ? this.#db.prepare("SELECT events.*, sessions.project_id FROM events JOIN sessions ON sessions.id = events.session_id WHERE events.session_id = ? ORDER BY sequence").all(sessionID)
      : this.#db.prepare("SELECT events.*, sessions.project_id FROM events JOIN sessions ON sessions.id = events.session_id ORDER BY events.session_id, sequence").all()
    return (rows as EventRow[]).map((row) => ({
      id: row.id,
      sessionID: row.session_id,
      projectID: row.project_id,
      sequence: row.sequence,
      eventType: row.event_type,
      source: row.source,
      occurredAt: row.occurred_at,
      payloadClass: row.payload_class,
      payload: row.payload,
      payloadHash: row.payload_hash,
      byteLength: row.byte_length,
      localReference: row.local_reference,
      sanitizerActions: JSON.parse(row.sanitizer_actions) as LedgerEvent["sanitizerActions"],
    }))
  }

  diagnostics(): number {
    const row = this.#db.prepare("SELECT COUNT(*) AS count FROM diagnostics").get() as { count: number }
    return row.count
  }

  queueConsolidation(sessionID: string): Promise<void> {
    return this.#enqueue(() => {
      const session = this.#db.prepare("SELECT id FROM sessions WHERE id = ?").get(sessionID) as { id: string } | undefined
      if (!session) return
      const now = new Date().toISOString()
      this.#db.prepare(
        "INSERT OR IGNORE INTO consolidation_queue (session_id, created_at, updated_at) VALUES (?, ?, ?)",
      ).run(sessionID, now, now)
    })
  }

  queuedConsolidations(limit = 1): string[] {
    if (!Number.isSafeInteger(limit) || limit < 1) throw new Error("limit must be a positive safe integer")
    return (this.#db.prepare(
      "SELECT session_id FROM consolidation_queue ORDER BY created_at, session_id LIMIT ?",
    ).all(limit) as Array<{ session_id: string }>).map((row) => row.session_id)
  }

  dequeueConsolidation(sessionID: string): Promise<void> {
    return this.#enqueue(() => {
      this.#db.prepare("DELETE FROM consolidation_queue WHERE session_id = ?").run(sessionID)
    })
  }

  status(): LedgerStatus {
    const count = (table: string) => (this.#db.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get() as { count: number }).count
    return {
      schemaVersion: SCHEMA_VERSION,
      integrity: this.integrityCheck(),
      events: count("events"),
      assertions: count("assertions"),
      quarantines: count("diagnostics"),
      pendingRetries: (this.#db.prepare("SELECT COUNT(*) AS count FROM consolidation_runs WHERE status = 'failed' AND attempts < 3").get() as { count: number }).count,
      queuedConsolidations: (this.#db.prepare("SELECT COUNT(*) AS count FROM consolidation_queue").get() as { count: number }).count,
      diskBytes: statSync(this.#dbPath()).size,
    }
  }

  async backup(destination: string): Promise<void> {
    mkdirSync(dirname(destination), { recursive: true, mode: 0o700 })
    await this.flush()
    await this.#backup(destination)
    chmodSync(destination, 0o600)
  }

  integrityCheck(): "ok" | string {
    const row = this.#db.prepare("PRAGMA integrity_check").get() as { integrity_check: string }
    return row.integrity_check
  }

  close(): void {
    activeLedgerOwners.delete(this.#ownerID)
    this.#db.close()
  }

  async flush(): Promise<void> {
    await this.#writeTail
  }

  #enqueue<T>(operation: () => T): Promise<T> {
    const write = this.#writeTail.then(operation)
    this.#writeTail = write.then(() => undefined, () => undefined)
    return write
  }

  #dbPath(): string {
    const row = this.#db.prepare("PRAGMA database_list").get() as { file: string }
    return row.file
  }

  #project(row: ProjectRow): ProjectRecord {
    const paths = this.#db.prepare("SELECT path FROM project_paths WHERE project_id = ? ORDER BY created_at")
      .all(row.id) as Array<{ path: string }>
    return {
      id: row.id,
      kind: row.kind,
      status: row.status,
      repositoryCommonDirectory: row.repository_common_directory,
      repositoryRemote: row.repository_remote,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      paths: paths.map((path) => path.path),
    }
  }

  #assertion(row: AssertionRow): AssertionRecord {
    return {
      id: row.id,
      projectID: row.project_id,
      scope: row.scope,
      category: row.category,
      content: row.content,
      confidence: row.confidence,
      status: row.status,
      model: row.model,
      createdAt: row.created_at,
      runID: row.run_id,
      sourceEventIDs: JSON.parse(row.source_event_ids) as string[],
      ...(row.supersedes_id ? { supersedesID: row.supersedes_id } : {}),
    }
  }

  #append(capture: SanitizedCapture): { inserted: boolean; sequence: number } {
    this.#db.exec("BEGIN IMMEDIATE")
    try {
      const existing = this.#db.prepare("SELECT sequence FROM events WHERE id = ?").get(capture.id) as { sequence: number } | undefined
      if (existing) {
        this.#db.exec("COMMIT")
        return { inserted: false, sequence: existing.sequence }
      }

      this.#db.prepare(
        `INSERT OR IGNORE INTO projects (
          id, workspace, kind, status, repository_common_directory, repository_remote, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      ).run("unscoped", null, "unscoped", "active", null, null, capture.occurredAt, capture.occurredAt)
      this.#db.prepare("INSERT OR IGNORE INTO sessions (id, project_id, created_at) VALUES (?, ?, ?)")
        .run(capture.sessionID, capture.projectID, capture.occurredAt)
      const sequenceRow = this.#db.prepare("SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence FROM events WHERE session_id = ?")
        .get(capture.sessionID) as { sequence: number }
      this.#db.prepare(
        `INSERT INTO events (
          id, session_id, sequence, event_type, source, occurred_at, payload_class, payload,
          payload_hash, byte_length, local_reference, sanitizer_actions
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).run(
        capture.id,
        capture.sessionID,
        sequenceRow.sequence,
        capture.eventType,
        capture.source,
        capture.occurredAt,
        capture.payloadClass,
        capture.payload,
        capture.payloadHash,
        capture.byteLength,
        capture.localReference,
        JSON.stringify(capture.sanitizerActions),
      )
      this.#db.exec("COMMIT")
      return { inserted: true, sequence: sequenceRow.sequence }
    } catch (error) {
      this.#db.exec("ROLLBACK")
      throw error
    }
  }

  #migrate(): void {
    this.#db.exec(`
      CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        workspace TEXT UNIQUE,
        kind TEXT,
        status TEXT,
        repository_common_directory TEXT,
        repository_remote TEXT,
        created_at TEXT,
        updated_at TEXT
      );
      CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id),
        sequence INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        source TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        payload_class TEXT NOT NULL,
        payload TEXT,
        payload_hash TEXT NOT NULL,
        byte_length INTEGER NOT NULL,
        local_reference TEXT,
        sanitizer_actions TEXT NOT NULL,
        UNIQUE(session_id, sequence)
      );
      CREATE TABLE IF NOT EXISTS diagnostics (
        id INTEGER PRIMARY KEY,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS operational_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS project_paths (
        project_id TEXT NOT NULL REFERENCES projects(id),
        path TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        PRIMARY KEY (project_id, path)
      );
      CREATE TABLE IF NOT EXISTS consolidation_runs (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id),
        project_id TEXT NOT NULL REFERENCES projects(id),
        start_sequence INTEGER NOT NULL,
        end_sequence INTEGER NOT NULL,
        status TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        next_retry_at TEXT,
        model TEXT,
        error_reason TEXT,
        owner_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(session_id, start_sequence, end_sequence)
      );
      CREATE TABLE IF NOT EXISTS consolidation_cursors (
        session_id TEXT PRIMARY KEY REFERENCES sessions(id),
        last_sequence INTEGER NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS consolidation_queue (
        session_id TEXT PRIMARY KEY REFERENCES sessions(id),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS assertions (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        scope TEXT NOT NULL,
        category TEXT NOT NULL,
        content TEXT NOT NULL,
        confidence REAL NOT NULL,
        status TEXT NOT NULL,
        model TEXT NOT NULL,
        created_at TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES consolidation_runs(id),
        supersedes_id TEXT REFERENCES assertions(id),
        source_event_ids TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        UNIQUE(run_id, fingerprint)
      );
      CREATE TABLE IF NOT EXISTS assertion_evidence (
        assertion_id TEXT NOT NULL REFERENCES assertions(id),
        event_id TEXT NOT NULL REFERENCES events(id),
        PRIMARY KEY (assertion_id, event_id)
      );
      CREATE INDEX IF NOT EXISTS events_session_sequence ON events(session_id, sequence);
    `)
    const schemaVersion = this.#db.prepare("SELECT MAX(version) AS version FROM schema_migrations")
      .get() as { version: number | null }
    if (schemaVersion.version !== null && schemaVersion.version > SCHEMA_VERSION) {
      this.#db.close()
      throw new Error("Ledger schema is newer than this plugin supports")
    }
    this.#ensureProjectColumns()
    this.#ensureConsolidationRunColumns()
    this.#db.exec("CREATE INDEX IF NOT EXISTS projects_remote ON projects(repository_remote); CREATE INDEX IF NOT EXISTS projects_common_directory ON projects(repository_common_directory);")
    const now = new Date().toISOString()
    this.#db.prepare(
      `INSERT OR IGNORE INTO projects (
        id, workspace, kind, status, repository_common_directory, repository_remote, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run("unscoped", null, "unscoped", "active", null, null, now, now)
    this.#db.prepare("UPDATE projects SET kind = 'unscoped', status = 'active', created_at = COALESCE(created_at, ?), updated_at = COALESCE(updated_at, ?) WHERE id = 'unscoped'")
      .run(now, now)
    this.#db.prepare("INSERT OR IGNORE INTO project_paths (project_id, path, created_at) SELECT id, workspace, ? FROM projects WHERE workspace IS NOT NULL")
      .run(now)
    this.#db.prepare("INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)")
      .run(SCHEMA_VERSION, now)
  }

  #ensureProjectColumns(): void {
    const existing = new Set(
      (this.#db.prepare("PRAGMA table_info(projects)").all() as Array<{ name: string }>).map((column) => column.name),
    )
    for (const [name, definition] of [
      ["kind", "TEXT"],
      ["status", "TEXT"],
      ["repository_common_directory", "TEXT"],
      ["repository_remote", "TEXT"],
      ["created_at", "TEXT"],
      ["updated_at", "TEXT"],
    ] as const) {
      if (!existing.has(name)) this.#db.exec(`ALTER TABLE projects ADD COLUMN ${name} ${definition}`)
    }
  }

  #ensureConsolidationRunColumns(): void {
    const existing = new Set(
      (this.#db.prepare("PRAGMA table_info(consolidation_runs)").all() as Array<{ name: string }>).map((column) => column.name),
    )
    if (!existing.has("owner_id")) this.#db.exec("ALTER TABLE consolidation_runs ADD COLUMN owner_id TEXT")
  }

  #recoverInterruptedConsolidations(): void {
    const now = new Date().toISOString()
    const running = this.#db.prepare("SELECT id, owner_id FROM consolidation_runs WHERE status = 'running'").all() as Array<{ id: string; owner_id: string | null }>
    for (const row of running) {
      if (row.owner_id && !ownerIsDead(row.owner_id, activeLedgerOwners)) continue
      this.#db.prepare(
        "UPDATE consolidation_runs SET status = 'failed', next_retry_at = ?, error_reason = 'consolidation interrupted', owner_id = NULL, updated_at = ? WHERE id = ? AND status = 'running'",
      ).run(now, now, row.id)
    }
  }
}

function ownerIsDead(ownerID: string, activeOwners: ReadonlySet<string>): boolean {
  if (activeOwners.has(ownerID)) return false
  const separator = ownerID.indexOf(":")
  const pid = Number(ownerID.slice(0, separator))
  if (!Number.isSafeInteger(pid) || pid < 1) return true
  if (pid === process.pid) return true
  try {
    process.kill(pid, 0)
    return false
  } catch {
    return true
  }
}

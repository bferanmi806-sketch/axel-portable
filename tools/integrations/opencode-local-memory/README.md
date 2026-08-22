# OpenCode Local Memory

This package is the adapter for Axel's local-first durable OpenCode memory system. Tickets 01 and 02 establish a safe plugin baseline and sanitized local capture ledger; live capture remains disabled.

## Compatibility

- OpenCode CLI: `1.18.5`
- `@opencode-ai/plugin`: `1.18.5` (MIT)
- `pointfish6660/opencode-memory-plugin`: commit `c0064bf3d83023ef4729d41aaa97eb8cf9ddf39a` (MIT, source reference only)
- Node.js: 24 or newer for the package's deterministic test path; OpenCode 1.18.5 runs plugins in Bun.

Dependencies are exact-pinned in `package.json` and `package-lock.json`. The reviewed upstream source is commit-pinned but is not an executable dependency in this baseline. Its current message hook combines capture, extraction triggering, and recall preparation, so later tickets will adapt reviewed components behind separate ports rather than enabling that combined hook directly.

The similarly named npm package `opencode-memory-plugin@0.5.5` is intentionally not installed. Its July 2026 published dependency graph differs from the reviewed repository manifest and includes high-severity advisories in transitive `sharp` and `drizzle-orm` paths.

## Safe Defaults

All subsystems default off:

```json
{
  "capture": false,
  "consolidation": false,
  "injection": false
}
```

Invalid switches fail closed. An invalid relative `dataDir` falls back to the platform default. Resolving the data layout does not create files or directories.

When OpenCode loads the adapter as a local plugin, use explicit environment variables because OpenCode 1.18.5 does not forward tuple options:

```powershell
$env:AXEL_OPENCODE_MEMORY_CAPTURE = "true"
$env:AXEL_OPENCODE_MEMORY_CONSOLIDATION = "false"
$env:AXEL_OPENCODE_MEMORY_INJECTION = "false"
$env:AXEL_OPENCODE_MEMORY_DATA_DIR = "C:\Temp\Axel\OpenCodeMemory"
```

## Capture Ledger

When `capture` is explicitly enabled in a future isolated profile, the adapter creates `memory.sqlite3` in the configured local data directory. The ledger uses short SQLite transactions, WAL journaling, a 5-second busy timeout, deterministic event IDs, per-session sequence numbers, duplicate detection, and a forward-only schema version check. It creates no database while capture is disabled.

Before any event is hashed or stored, one policy boundary:

- Excludes configured workspaces, tools, and path roots.
- Redacts authorization headers, bearer/API tokens, credential assignments, private-key blocks, and configured literal content patterns.
- Limits text bodies to 64 KiB by default.
- Stores oversized or binary payloads as a class, byte count, and SHA-256 hash without a body.
- Accepts a local reference only for an existing regular file below the configured `references/` root.

Sanitizer metadata records the action category, never the removed value. Malformed payloads are quarantined as content-free diagnostics. The `SanitizedCapture` type is the only ledger write input and is the only payload shape a future consolidator may consume.

The implementation uses a small runtime adapter: Node uses the built-in `node:sqlite` API, while OpenCode's Bun runtime uses its built-in `bun:sqlite` API. This avoids a native npm dependency and keeps the ledger isolated behind `Ledger`. Bun backups checkpoint WAL and serialize the database; Node uses the documented SQLite backup operation. Both paths use prepared statements for values. Node documents that bound parameters protect against SQL injection: https://nodejs.org/api/sqlite.html#class-statementsync

## Project Registry

When capture is enabled, each workspace resolves to a project before its first event is written. Resolution is deterministic:

1. An existing normalized workspace path wins.
2. A Git worktree with a known Git common directory joins its existing project.
3. Otherwise a new project is created, including non-repository directories.

Remote URLs are intentionally not automatic identity. A second clone of the same remote becomes a distinct project and reports the matching remote projects as reconciliation candidates. Use `ProjectRegistry.previewReconciliation(directory)` to inspect candidates and `reconcile(projectID, directory)` only after choosing the project to preserve. Reconciliation adds the new path as an alias, updates current Git evidence, and retains all existing session history under the selected project ID.

The read-only `memory_project_list` tool is present only while an isolated capture ledger is open. It reports project IDs, paths, Git evidence, state, and timestamps. It does not create a database in disabled mode.

## Consolidation

When both `capture` and `consolidation` are enabled in an isolated profile, a session idle or compaction boundary queues work for at most 24 KiB of text events that have already passed the capture policy. The next user system-transform hook drains one queued range. It creates a titled internal OpenCode session, registers the session as internal, disables tools, and prompts the configured active default model without a model override. Idle hooks do not make provider calls, and queued work survives plugin disposal.

The model receives an explicit untrusted-data instruction and must return JSON assertions only. Assertions are validated before storage: valid categories and scopes, known source event IDs, finite confidence, bounded content, no credential-like content, and stricter evidence rules for personal scope. Assertions retain model identity, source event IDs, confidence, status, and supersession links.

Consolidation runs are cursor-based and idempotent per event range. Failures store only a generic reason and an exponential retry time, capped at three attempts; retries retain their original event range so newly appended events cannot reset the attempt count. Exhausted ranges advance the cursor without creating assertions. Running rows are owned by their active ledger instance, and only stale owners are recovered after reopen. The production runner uses the documented OpenCode `session.create` and `session.prompt` SDK operations with a bounded prompt timeout: https://opencode.ai/docs/sdk

No real provider call has been made during development because the live plugin remains disabled. The runner contract is covered with a fake SDK client; enable it first only in an isolated OpenCode profile after reviewing your model-provider boundary.

## Unified Recall Policy

`UnifiedRecallPolicy` is the read-only policy boundary for future Codemem, Basic Memory, and local-ledger adapters. It runs source searches in parallel, fails open when one source is unavailable, filters to current and sufficiently confident records, enforces project scope and unsafe-content checks, ranks personal/project/session context deterministically, deduplicates equivalent facts, and enforces a hard byte budget. Every injected line retains its source handle. The policy does not write memories or create a new store.

Adapters should map their records to `RecallRecord` and keep source-specific authentication, synchronization, and write behavior outside this package. The paused global plugin rollout remains unchanged.

The package includes two read-only adapters:

- `BasicMemoryMarkdownSource` reads an explicitly configured local Markdown project. Profile and preference directories are personal scope; other notes are returned as project scope only when they match the request query.
- `CodememCliSource` invokes an explicitly configured `codemem` CLI without a shell, uses bounded JSON output and a timeout, defaults to the current project rather than `--all-projects`, and requires explicit project-ID mappings before accepting all-project results.

Neither adapter writes memory, edits notes, or changes global configuration. A host must explicitly pass them through `PluginDependencies.recallSources`.

Default Windows layout:

```text
%LOCALAPPDATA%/Axel/OpenCodeMemory/
|-- memory.sqlite3
|-- backups/
|-- diagnostics/
`-- references/
```

## Development

```powershell
npm ci --ignore-scripts
npm test
```

The install uses `--ignore-scripts` so new dependencies cannot execute lifecycle scripts before review.

## Isolated Plugin Test

1. Build with `npm test`.
2. Use `examples/smoke-config` as the disposable workspace; its `.opencode/plugins/local-memory.js` is the documented local-plugin entry.
3. Set `AXEL_OPENCODE_MEMORY_CAPTURE`, `AXEL_OPENCODE_MEMORY_CONSOLIDATION`, `AXEL_OPENCODE_MEMORY_INJECTION`, and `AXEL_OPENCODE_MEMORY_DATA_DIR` explicitly in the disposable process environment.
4. Start OpenCode with `--dir examples/smoke-config` and verify the ledger after the model turn.
5. Keep all subsystem switches false for Ticket 01 verification.

OpenCode supports local TypeScript/JavaScript plugins from `.opencode/plugins/` and npm plugin entries as documented at https://opencode.ai/docs/plugins/. OpenCode 1.18.5 does not pass arbitrary tuple options from the `plugin` config list to a local factory, so this adapter uses explicit environment variables when it is loaded as a local plugin. Configuration uses the published schema at https://opencode.ai/config.json.

Do not add this package to the live global config yet. OpenCode loads configuration and plugins at startup, so any future config change requires a full OpenCode restart.

## Disable And Remove

Disable the adapter by removing its plugin entry or setting all subsystem switches to false, then restart OpenCode. Removing the plugin entry or package does not delete the local data directory. Data removal will remain a separate, explicit operation.

## Current Boundaries

- `CapturePort`: event, message, tool, and compaction capture hooks.
- `ConsolidationPort`: idle and compaction session boundaries.
- `InjectionPort`: system-context injection.
- `InternalSessionRegistry`: excludes memory-owned sub-sessions from every boundary.
- `PluginLogger`: privacy-safe warning reporting that cannot become a second failure path.
- `ProjectRegistry`: workspace identity, project inspection, and explicit move reconciliation.
- `ConsolidationService`: bounded, source-linked assertion synthesis with durable cursor and retry state.

Every port call is fail-open: adapter errors are reported and swallowed so ordinary OpenCode work continues.

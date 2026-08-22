---
title: Local-First OpenCode Memory Project
type: project
permalink: axel/current-work/local-first-open-code-memory-project
tags:
- opencode
- memory
- active-project
---

## Goal
Build a global, local-first OpenCode memory system that automatically preserves useful personal and project context without requiring explicit `remember this` prompts.

## Settled Decisions
- Integrate globally inside OpenCode.
- Keep all durable storage on this computer.
- Use the active OpenCode model initially for consolidation; only sanitized context may cross the configured model-provider boundary.
- Retain permitted text indefinitely.
- Store only bounded metadata, hashes, and allowed local references for oversized or binary outputs.
- Use a sanitized append-oriented SQLite ledger as the authoritative history.
- Treat extracted assertions and indexes as derived, source-linked, and rebuildable.
- Adopt `pointfish6660/opencode-memory-plugin` with adapters rather than building a memory framework from scratch.

## Plan
The canonical specification and seven dependency-ordered implementation tickets are under `.planning/opencode-local-memory/` in `C:/Users/bfera/Documents/paseo-codex-test`.

## Status
Planning complete on 2026-07-26. Implementation has not started. Ticket 01 is the implementation frontier.


## Implementation Update
Ticket 01 completed on 2026-07-26. An isolated TypeScript adapter package now exists at `opencode-local-memory/`. It pins OpenCode/plugin SDK 1.18.5, keeps capture/consolidation/injection disabled by default, uses fail-open hook boundaries, identifies internal sessions, and has passing typecheck/test/audit verification. No live global OpenCode configuration or memory storage was changed.

The published `opencode-memory-plugin@0.5.5` was not adopted as an executable dependency after its published dependency graph was found to differ from the reviewed upstream source and to contain high-severity advisory paths. The upstream repository remains a commit-pinned source reference.


## Implementation Update: Ticket 02
Ticket 02 completed on 2026-07-26. The adapter now has a versioned SQLite ledger using Node's built-in `node:sqlite` API, a serial transactional writer, WAL mode, per-session sequences, deduplication, integrity checks, schema-version refusal, and content-free malformed-event diagnostics. A unified policy excludes configured workspaces/tools/paths and redacts secrets before IDs, hashes, or writes. Oversized/binary bodies are omitted. Tests, typecheck, and npm audit pass; live capture remains disabled. Ticket 03, stable project identity and workspace registry, is next.


## Implementation Update: Ticket 03
Ticket 03 completed on 2026-07-26. The SQLite ledger now has a project registry with normalized workspace paths/aliases, Git common-directory and remote evidence, timestamps, and active status. Exact path and linked-worktree matches reuse a project; same-remote clones remain separate and only appear in reconciliation previews. Explicit reconciliation preserves session history and prevents overwriting an occupied path. A read-only `memory_project_list` tool is available when an isolated capture ledger is open. The complete suite has 32 passing tests, typecheck passes, and npm audit is clean. Live capture remains disabled. Ticket 04, source-linked model consolidation, is next.


## Implementation Update: Ticket 04
Ticket 04 completed on 2026-07-26. Schema version 3 adds source-linked assertions, evidence, supersession, consolidation runs, cursors, and bounded retry state. Consolidation selects only sanitized text under a 24 KiB budget, validates typed assertion JSON, requires source IDs, applies stricter personal-memory evidence rules, and preserves supersession history. The OpenCode runner creates a marked internal session, enumerates and disables every tool, and uses the active default model without an override. It is covered by deterministic fake-client and idle-boundary tests. No real provider request was made because global capture/consolidation remains disabled; run an isolated provider smoke test before enabling it. Ticket 05, bounded relevant recall and context injection, is next.


## Remaining Ticket Attempt
On 2026-07-26, implementation began for Tickets 05-07: bounded source-handle recall, memory inspection/correction/forget/rebuild/export controls, privacy-safe status, and SQLite backups. The suite reached 44 passing tests with clean typecheck/audit.

Global rollout is blocked. Multiple disposable `opencode run` smoke tests returned the expected model text but produced zero adapter ledger events. OpenCode resolved both direct-file and auto-discovered plugin paths in `debug config`, but did not evaluate or invoke the adapter module/hooks. A temporary global bridge was removed. Do not enable the plugin in global config until the local-plugin loader behavior is diagnosed and a disposable ledger records captured events.


## Pause Decision
On 2026-07-27, work on diagnosing the OpenCode plugin loader, proving ledger event capture, and proceeding to Ticket 05 is paused for now. Global rollout remains disabled. Resume only when this project is intentionally revisited.


## Architecture Clarification
The memory system is not dependent on the local SQLite ledger. Codemem already provides automatic cross-session memory, and Basic Memory provides structured durable personal and project context. These are currently sufficient for Axel's memory needs. Cognee should not be adopted merely to replace persistence; any future evaluation would need to demonstrate a concrete recall or knowledge-graph benefit beyond the existing systems.
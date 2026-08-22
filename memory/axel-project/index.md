---
project_id: AXEL
plane_project_id: 5b59d8b8-f4fc-4430-a048-01fb453a80ab
workspace_id: axel_project
document_type: workspace_index
topic: active Axel project knowledge map
status: active
last_verified: 2026-07-14
source_type: verified_primary_sources
---

# Axel project knowledge

Axel is a conversation-first daily companion and project partner. The primary interface should remain calm and conversational; governed tools and background workers support the conversation instead of replacing it with a dashboard or autonomous build system.

This workspace stores durable understanding, decisions, explanations, and compact approved summaries. It is not a task tracker. Plane remains the source for formal progress, priorities, deadlines, cycles, modules, and work items.

## Active map

- [Current architecture](architecture/current-architecture.md) — runtime, history, memory, project-awareness, and worker boundaries.
- [Active decisions](decisions/active-decisions.md) — implemented, planned, deferred, and superseded choices.
- [Conversation modes](features/conversation-modes.md) — Conversation V2 and the rollback-only legacy path.
- [Project knowledge](features/project-knowledge.md) — Plane, primary Markdown, evidence workspaces, retrieval, and governed writes.
- [Memory, sessions, and Codex Worker](features/memory-sessions-and-worker.md) — durable memory and heavy-analysis boundaries.
- [Time-system ownership](features/time-system-ownership.md) — where deadlines, scheduled work, and actual time belong.

## Current cautions

- Plane and this workspace have different jobs. If they disagree, report the conflict; do not silently inspect `axel_backend` to settle it.
- The live AXEL Plane project still contains onboarding/template material and lists some already implemented project-awareness work as backlog. Reconciliation belongs in Plane; this workspace does not reproduce those work items.
- `axel_backend` is linked evidence, not the whole project. Normal project conversation must not inspect it.
- Google Calendar ownership of scheduled work and Kimai ownership of actual time are planned architecture boundaries for this project-state model, not completed integrations.

## Retrieval rule

Read this file first, then only the smallest relevant set of active linked documents. Ignore archived or superseded documents unless historical context is requested.

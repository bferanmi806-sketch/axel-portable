---
project_id: AXEL
plane_project_id: 5b59d8b8-f4fc-4430-a048-01fb453a80ab
workspace_id: axel_project
document_type: feature
topic: Conversation Mode V1 and V2 boundaries
status: active
last_verified: 2026-07-14
source_type: verified_repository_docs_code_and_tests
---

# Conversation modes

## Conversation V2

Conversation V2 is Axel's default OpenAI Agents SDK runtime. It requires a command and stable `session_id`, streams safe task events, uses registered governed tools, and persists model-facing history only through SDK `SQLiteSession`.

The V2 tool registry is metadata-driven. Permission and cost policy are deterministic runtime boundaries. Project reads use `project_state`; durable-memory reads and proposals stay separate; registered workspace analysis uses Codex Worker when enabled.

## Legacy Conversation Mode

The legacy `/ws/command` conversation path remains available only as an explicit rollback route. Legacy orchestration, routing patches, history semantics, and UI payload compatibility must remain until rollback retirement is separately approved and soak/recovery criteria are satisfied.

Switching between legacy and V2 can create context discontinuity because their history semantics differ. One normal turn must never be submitted to both runtimes.

## Current limitations

- Only the direct OpenAI provider is implemented for Conversation V2.
- The live-provider acceptance suite is opt-in because it requires configured external services.
- Legacy code is intentionally still present and protected by regression tests.

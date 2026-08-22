---
project_id: AXEL
plane_project_id: 5b59d8b8-f4fc-4430-a048-01fb453a80ab
workspace_id: axel_project
document_type: feature
topic: approved memory SQLite sessions RuntimeStore and Codex Worker
status: active
last_verified: 2026-07-14
source_type: verified_repository_docs_runtime_config_and_approved_memory
---

# Memory, sessions, and Codex Worker

## Three distinct stores

- `SQLiteSession`: canonical Conversation V2 model history for one stable conversation ID.
- `RuntimeStore`: V2 audit/display mirror and legacy compatibility store; not V2 model input.
- Approved long-term memory: durable recalled context with approval and lifecycle filtering; not conversation history.

These boundaries prevent duplicated turns and prevent unapproved or superseded memories from quietly steering current answers.

Approved active memory verified on 2026-07-14 includes the product principle that Axel should stay conversation-first. Another approved record confirms the user has enabled the Codex worker capability. Neither record grants unrestricted filesystem access or automatic permanent writes.

## Codex Worker

Codex Worker is a governed registered-workspace analysis path. The current environment enables the worker and real `codex_exec` runner, but the security boundary remains workspace resolution, safe source selection, staging, hashing, output validation, and contained artifact publication.

Worker outputs are drafts and evidence artifacts, not automatically approved project truth. For project knowledge:

1. `project_state` identifies that heavy evidence is required.
2. Axel presents the Codex escalation and obtains approval for the expanded work.
3. Codex produces a full analysis artifact and compact verified summary.
4. Only an approved compact summary is saved into `axel_project` and linked from `index.md`.

The worker never writes `SQLiteSession`, invokes other model tools, or silently promotes findings into long-term memory or primary knowledge.

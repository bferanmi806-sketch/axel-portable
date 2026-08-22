---
project_id: AXEL
plane_project_id: 5b59d8b8-f4fc-4430-a048-01fb453a80ab
workspace_id: axel_project
document_type: decision_register
topic: active planned deferred and superseded architecture decisions
status: active
last_verified: 2026-07-14
source_type: implementation_brief_verified_code_docs_and_tests
---

# Active decisions

## Implemented and active

- Axel remains conversation-first; project systems support the conversation rather than hijacking ordinary questions.
- Conversation V2 uses the OpenAI Agents SDK and a stable `SQLiteSession` as its only model-facing conversation history.
- `RuntimeStore` is audit/display only for V2.
- Approved long-term memory is separate from session history and excludes superseded records from current guidance.
- Project capabilities are exposed to the model through one governed read tool, `project_state`, rather than a generic set of separate Plane/workspace/repository tools.
- Plane is authoritative for formal project state. Primary Markdown is authoritative for guided durable understanding.
- Project-to-workspace linking reuses `WorkspaceRegistry`, saved associations, workspace permissions, and `ProjectStateService`.
- Primary retrieval starts at `index.md`, follows safe Markdown links, ranks a bounded relevant set, and filters inactive lifecycle states.
- Evidence workspaces are secondary. Narrow explicit verification uses bounded Project Understanding/Tree-sitter retrieval; heavy analysis escalates to Codex Worker.
- Workspace folder creation and Markdown saves use existing governed, contained tools. Markdown saves are create-only and do not overwrite.

## Planned, not implemented in this model

- Google Calendar should own scheduled work blocks.
- Kimai should own actual time recorded.
- Compact approved Codex summaries may be added to the primary workspace and linked from `index.md` after review.
- Plane data should be reconciled so implemented project-awareness work and template onboarding content accurately reflect reality.

## Deferred

- Kimai implementation is explicitly outside the current task.
- Broad automatic repository inspection during normal conversation is deferred and prohibited by the current boundary.
- Automatic rewriting, deletion, or semantic supersession of important primary knowledge remains deferred; confirmation is required.

## Superseded or rejected

- Treating the repository workspace as the whole AXEL project is superseded by primary-plus-evidence roles.
- Guessing a workspace from a project or folder name is rejected; stable saved IDs are required.
- A generic multi-tool redesign for project capabilities is rejected in favour of the single governed `project_state` read boundary.
- Using `RuntimeStore`, UI history, or LangGraph checkpoints as additional Conversation V2 model history is superseded by `SQLiteSession`.

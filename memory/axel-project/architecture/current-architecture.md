---
project_id: AXEL
plane_project_id: 5b59d8b8-f4fc-4430-a048-01fb453a80ab
workspace_id: axel_project
document_type: architecture
topic: current Axel runtime and project-awareness architecture
status: active
last_verified: 2026-07-14
source_type: verified_repository_docs_code_and_tests
---

# Current architecture

## Product boundary

Axel is conversation-first. It should help with daily conversation, safe memory, time management, project understanding, learning, and governed tool use while keeping the main experience calm. Long or evidence-heavy work belongs behind bounded background-worker paths.

## Conversation runtime

- Conversation V2 is the default backend route at `/ws/conversation-v2`.
- The legacy `/ws/command` conversation path remains a rollback boundary and still supports non-conversation compatibility consumers.
- V2 uses OpenAI Agents SDK `0.18.0`; the SDK agent receives tools from the metadata-driven Conversation V2 registry.
- Deterministic permission and cost controls sit outside model instructions.

## History and runtime state

- Agents SDK `SQLiteSession`, keyed by the stable UI `session_id`, is the only model-facing Conversation V2 history source.
- `RuntimeStore` is an audit/display mirror for V2 with `model_history_source=false`; it is not reinjected into model context.
- UI history, LangGraph checkpoints, and legacy in-memory history are not additional V2 model-history sources.

## Approved long-term memory

- Long-term memory is separate from SDK session history.
- Conversation V2 exposes read and proposal boundaries, not unrestricted memory management.
- Only approved, committed, active durable memories are eligible for recall. Pending, rejected, ambiguous, duplicate, transcript-sized, or superseded records must not guide current answers.
- Current explicit user instructions override stored preferences.

## Project awareness

- Plane owns structured project management.
- The linked primary Markdown workspace owns durable project understanding.
- `ProjectStateService` resolves Plane first, then the saved `WorkspaceRegistry` association, then uses `index.md` to select focused active primary documents. Approved active memory may supplement the result.
- The single model-facing `project_state` read tool returns Plane facts, primary knowledge, selected active memory, and labelled inferences as distinct evidence types.
- A normal project question does not inspect evidence workspaces.
- An explicit narrow verification request may use the configured bounded Tree-sitter scope in `axel_backend`.
- A broad or heavy evidence request returns a Codex Worker escalation recommendation instead of scanning the repository in conversation.

## Workspace association

The stable saved association is:

- Plane project ID: `5b59d8b8-f4fc-4430-a048-01fb453a80ab`
- Plane identifier: `AXEL`
- primary workspace: `axel_project`
- evidence workspace: `axel_backend`

The association is generic and ID-based. No folder-name guessing or AXEL-only registry exists.

## Background systems

- LangGraph remains a background workflow/checkpoint boundary and is not the V2 conversation-history source.
- Node-RED remains an external scheduler/trigger layer and is not called during ordinary V2 turns.
- Codex Worker runs governed registered-workspace analysis, stages selected evidence, validates draft artifacts, and does not write conversation history or permanent project truth.

---
project_id: AXEL
plane_project_id: 5b59d8b8-f4fc-4430-a048-01fb453a80ab
workspace_id: axel_project
document_type: feature
topic: scalable primary project knowledge and evidence retrieval
status: active
last_verified: 2026-07-14
source_type: verified_implementation_and_tests
---

# Project knowledge

## Source ownership

- Plane: projects, modules, work items, cycles, statuses, priorities, deadlines, and formal progress.
- `axel_project`: durable explanations, decisions, principles, lessons, methods, conclusions, limitations, and compact approved summaries.
- `axel_backend`: secondary code and document evidence.
- Approved active memory: personal or durable context that passed the memory approval boundary.

The primary workspace is not a second task tracker and must not copy Plane work-item state.

## Normal retrieval

1. Resolve the Plane project by stable ID, identifier, or unique name.
2. Load the saved association from `WorkspaceRegistry`.
3. Query Plane when progress, deadlines, modules, cycles, or structured state matter.
4. Read primary `index.md`.
5. Follow only safe linked Markdown paths and select a bounded relevant set.
6. Exclude archived, inactive, rejected, deleted, and superseded knowledge unless history is requested.
7. Add approved active memory when useful.
8. Keep source facts and service inferences distinct.

Unlinked files are not recursively read in normal retrieval. A missing association or missing `index.md` is reported clearly; neither is guessed.

## Secondary evidence

- Ordinary architecture or status questions do not inspect `axel_backend`.
- Explicit requests to check the backend, verify implementation, or inspect source evidence permit only the configured bounded scope.
- Broad repository analysis, full audits, and all-file requests return a Codex Worker escalation recommendation.
- Codex Worker does not run automatically from `project_state`; user approval remains the boundary for expanding into heavy analysis.

## Writing

Axel may create project knowledge after an explicit request, user approval, or an approved low-risk maintenance rule. Safe writes include new active notes, approved summaries, links, and folders inside the registered primary workspace.

Confirmation is required before changing project purpose, superseding major decisions, deleting important knowledge, converting uncertainty into accepted fact, or rewriting personal reflections. The current governed Markdown writer is contained, create-only, rejects traversal and absolute paths, and never writes to `axel_backend` because that workspace lacks save permission.

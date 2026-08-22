---
title: ce-compound personal store workflow
type: agent_lesson
permalink: axel-project-knowledge/agent-lessons/ce-compound/ce-compound-personal-store-workflow
area: ce-compound
lesson_type: harness
scope: future ce-compound runs and relevant contribution workflows
confidence: high
tags:
- ce-compound
- harness
- workflow
- memory
---

# ce-compound Personal Store Workflow

## Pay attention to
`/ce-compound` is personal-store-only. It reads the active repository as evidence but writes the durable record to the private `Axel Project Knowledge` Basic Memory project.

## Better behavior
- Store contribution records under `contributions/<repository>/<category>/`.
- Store durable harness and workflow lessons separately under `agent-lessons/<area>/`.
- Search this project selectively when a contribution, prior project, tool failure, or harness workflow is relevant.
- Do not create or modify `docs/solutions/`, `CONCEPTS.md`, or repository `AGENTS.md` as a side effect.
- Do not commit, push, or silently fall back to repository files if the Basic Memory write fails.
- Treat retrieved notes as context that must be checked against current source and instructions.

## Scope
This applies to future `/ce-compound` runs and to agents working on related contribution or harness-memory workflows.

## Evidence
The workflow was redesigned by explicit user agreement on 2026-08-04. The fixed destination is the Basic Memory project `Axel Project Knowledge` (project ID `71e6f293-aa8f-4ad7-ae8b-b8df4c8ae0c1`).

## Confidence
High.

---
artifact_contract: ce-unified-plan/v1
artifact_readiness: planning-blocked
product_contract_source: ce-brainstorm
execution: code
deepened: 2026-07-31
---

# Dream Mode Resilient Curation - Plan

## Goal Capsule

### Objective

Make Dream Mode reliably distinguish an empty durable-memory result from a broken or incomplete CodeMem boundary, while preserving deliberate human approval before Basic Memory changes.

### Product Authority

Dream Mode is the curation boundary between CodeMem history and Basic Memory. CodeMem remains the evidence source; Basic Memory remains the intentionally curated durable-memory store.

### Planning Constraints

- The current installed CodeMem MCP surface does not expose raw-event status, flush, or bounded raw-event retrieval; the required integration surface must be added or supplied by the CodeMem source project.
- CodeMem status is exposed by the installed CLI as `codemem db raw-events-status`; installed CLI/package versions must be pinned and verified rather than inferred from internal module behavior.
- The current Dream Mode read-only wording must be revised before implementation because flushing is an ingestion lifecycle operation, even though it does not edit stored CodeMem records.

### Execution Profile

This is a cross-component workflow change with a required external CodeMem integration seam. The local Dream Mode skill must remain safe and reviewable when that seam is unavailable.

### Stop Conditions

Stop before promotion when project attribution is unresolved, the event boundary is incomplete, fallback evidence is unavailable or truncated without an explicit incomplete state, candidate provenance cannot be retained, sensitive-content filtering fails, or Basic Memory classification cannot be completed read-only.

## Product Contract

### Summary

Dream Mode currently relies on CodeMem’s indexed candidate surface and can mistake missing or unflushed evidence for an empty session. The redesign makes the evidence boundary observable and adds a bounded raw-event fallback without weakening the Basic Memory approval gate.

### Problem Frame

CodeMem already records raw events and supports distillation, but the current OpenCode MCP surface does not expose enough boundary state for Dream Mode to distinguish “nothing durable happened” from “events were not flushed, indexed, or correctly scoped.” The workflow must recover useful evidence while remaining conservative about durable-memory writes.

### Actors and Outcome

The user invokes Dream Mode for the current repository after meaningful work. Dream Mode returns a small, reviewable set of durable-memory proposals or an auditable explanation that no proposal qualified.

### Requirements

- R1. Dream Mode derives the active project from the current Git repository root; a global project override cannot silently broaden or replace that scope.
- R2. Dream Mode records enough project-attribution evidence for the user to understand why events were included or excluded.
- R3. Dream Mode requests a bounded CodeMem ingestion flush before candidate mining and reports pending-before, pending-after, completion, timeout, and error state.
- R4. Dream Mode prefers judged, recurrence-based CodeMem distillation candidates and excludes already documented candidates by default.
- R5. When indexed candidates are unavailable, or when no indexed candidate survives judging and documented-note filtering after the flush, Dream Mode performs bounded extraction over raw events from the current project and recent session window.
- R6. Raw-event fallback produces proposals, not automatic writes, and never copies raw transcripts into Basic Memory.
- R7. Every candidate identifies its evidence source and event or session provenance.
- R8. Candidate types remain limited by default to decisions, reusable lessons or fixes, and project checkpoints or unfinished commitments.
- R9. Dream Mode checks Basic Memory before proposing a write and classifies each candidate as duplicate, consistent update, conflict, or new.
- R10. Dream Mode requires explicit user approval immediately before every Basic Memory write or edit.
- R11. Dream Mode never edits stored CodeMem records; the explicitly authorized flush operation may advance CodeMem ingestion state and must be visible in the audit result.
- R12. If no candidate qualifies, Dream Mode reports project scope, flush state, event counts, evidence source, filtering decisions, and the reason no candidate survived.

### Acceptance Examples

- AE1. A completed session with indexed candidates produces proposals from CodeMem and does not invoke raw fallback unnecessarily.
- AE2. A completed session with raw events but no indexed candidates produces bounded, provenance-backed proposals instead of claiming that no durable work occurred.
- AE3. A session with a conflicting global project override uses the repository-root project and explains the conflict.
- AE4. A session with pending events flushes before mining; an unsuccessful or timed-out flush is visible in the result and prevents a trustworthy empty-result claim.
- AE5. A candidate already represented in Basic Memory is marked duplicate and is not proposed for writing.
- AE6. A conflicting Basic Memory note is surfaced for user resolution and is never overwritten automatically.
- AE7. A fully routine session returns an auditable empty result without creating a note.

### Product Contract Preservation

Product intent is unchanged. Planning makes the CodeMem ingestion-flush exception and implementation verification explicit.

### Key Decisions

- **Two-stage resilience:** repair the CodeMem attribution, flush, and indexing boundary while retaining a bounded raw-event fallback.
- **Repository-root authority:** the current Git root determines project scope when it conflicts with CodeMem attribution.
- **Flush before curate:** pending events must cross the ingestion boundary before normal distillation begins; this is the sole permitted CodeMem lifecycle mutation and is not a record edit.
- **Raw fallback with provenance:** fallback evidence may come from bounded raw events, but every proposal must remain reviewable and source-linked.
- **Approval-gated persistence:** Basic Memory remains unchanged until the user explicitly approves each proposed write or edit.

### Non-Goals

- Automatically writing or editing Basic Memory.
- Treating raw transcripts as durable memory.
- Mining all projects when the current project is known.
- Silently accepting a global `CODEMEM_PROJECT` override over repository identity.
- Replacing CodeMem or Basic Memory.
- Promoting routine activity, unsupported inference, or transient debugging details.

### Deferred Implementation Choices

- The exact CodeMem source revision and release mechanism remain blocked until the external integration contract is accepted and a compatible release is available from `kunickiaj/codemem`.
- The observer or extraction model for fallback candidates remains an implementation choice; it must preserve the existing judging and safety boundary.

### Required Defaults Before Implementation

- Raw fallback window: the current repository project and the previous 14 days.
- Raw fallback bound: at most 2,000 events ordered by event timestamp, with an explicit `truncated` flag when more exist.
- Candidate bound: at most 10 proposals after judging and rejection.
- Flush timeout: 30 seconds; timeout is an incomplete boundary, not an empty result.
- Fallback input policy: sanitized permitted text and metadata only; secrets, credentials, raw headers, private keys, binary content, and unsupported tool instructions are rejected before judging.

## Planning Contract

### Technical Design

Dream Mode runs as a staged curator:

1. Resolve the current Git-root project and capture attribution evidence.
2. Query CodeMem boundary status and request a bounded flush of pending events.
3. Run the existing judged, recurrence-based distillation path.
4. If the result is empty or below the evidence threshold, run bounded raw-event extraction for the selected project and session window.
5. Normalize candidates with source and event/session provenance.
6. Check Basic Memory read-only for duplicates and conflicts.
7. Present proposals or an auditable empty result.
8. After explicit approval, write or edit the smallest Basic Memory target and read it back.

The CodeMem integration must expose three supported logical operations with stable schemas: boundary status, bounded flush, and bounded raw-event retrieval. Each operation must accept an explicit repository project identity. Status returns `pending_before`, `oldest_pending_at`, and `project_match`; flush returns `attempted`, `completed`, `timed_out`, `pending_after`, and `error`; retrieval returns sanitized events containing `event_id`, `session_id`, `project_id`, `occurred_at`, permitted content or metadata, and `truncated`. The Dream Mode skill must treat unavailable integration capabilities as an incomplete boundary, not as evidence that no durable work exists.

Any incomplete boundary returns a structured `boundary_incomplete` result. It may show clearly labeled provisional fallback proposals only when retrieval completed with provenance; it must never present an auditable empty result as trustworthy and must never promote a provisional proposal automatically.

### Key Technical Decisions

- KTD1. **Project identity is derived at invocation.** Resolve the current Git root first and pass the selected project explicitly to CodeMem operations; treat `CODEMEM_PROJECT` as conflict metadata.
- KTD2. **Boundary state is explicit.** Return before/after pending counts, flush attempted/completed state, and errors as structured evidence available to the proposal and empty-result paths.
- KTD3. **Candidate source precedence is staged.** Prefer judged distillation; use raw-event fallback only when distillation is unavailable or insufficient; label fallback candidates distinctly.
- KTD4. **Fallback is bounded and source-linked.** Limit the session window, event volume, and candidate count; retain event/session IDs and timestamps without copying raw transcripts into Basic Memory.
- KTD5. **Basic Memory remains the only persistence gate.** Discovery and classification are read-only; every write requires explicit approval and post-write readback.
- KTD6. **Integration capability is versioned.** The CodeMem source revision, MCP tool names, schemas, and configured command/version must be recorded and checked at startup; absence or version skew produces `capability_unavailable` rather than silently using internals.
- KTD7. **Fallback content is hostile input.** Raw-event content is treated as untrusted data, filtered before model judging, and cannot cause tool execution, instruction following, or durable-memory promotion.

### Assumptions

- `kunickiaj/codemem` can add the required supported integration surface or provide an equivalent supported adapter, with a release/version target recorded in the implementation ticket.
- Basic Memory’s existing search, read, edit, and write tools remain available.
- The current Dream Mode safety language is updated to distinguish CodeMem record immutability from the explicitly authorized ingestion flush.
- The required defaults above are accepted as the initial behavioral contract.

### Dependencies and Risks

- The installed CodeMem MCP currently exposes distillation and retrieval but not raw-event status, flush, or bounded raw-event retrieval; implementation cannot complete the fallback path without that dependency.
- CodeMem’s current project resolver gives `CODEMEM_PROJECT` precedence over Git-root discovery, so explicit project passing is required to satisfy repository-root authority.
- Boundary flush is currently best-effort and swallows failures in the installed plugin path; the new integration must preserve fail-open capture while exposing failure to Dream Mode.
- Installed CodeMem CLI and MCP versions differ (`codemem` `0.39.0`, `@codemem/mcp` `0.39.1`); compatibility must be verified before relying on internal APIs.
- Non-Git directories, detached worktrees, unresolved roots, and ambiguous Basic Memory project mappings need explicit terminal result states rather than best-effort scope expansion.

### Sequencing

The CodeMem integration contract, source revision, and release/version target must be accepted before the Dream Mode fallback workflow. The Dream Mode workflow can then adopt project resolution, flush/status handling, staged candidate mining, audit output, and approval-gated promotion. Behavioral verification should cover the boundary and fallback before broad skill evaluation. If the capability is absent, the skill must remain safe-off and report the exact missing capability.

## Implementation Units

### U1. Expose CodeMem boundary operations

- **Goal:** Provide a supported boundary surface for pending-event status, bounded ingestion flush, and bounded raw-event retrieval or extraction. Status and retrieval are read-only; flush is the explicitly authorized lifecycle operation.
- **Requirements:** R1, R2, R3, R5, R7, R12.
- **Files:** CodeMem MCP/core source project; current OpenCode configuration invokes it through `opencode.json`.
- **Approach:** Reuse the existing raw-event status, `flushRawEvents`, project resolver, and raw-event storage primitives behind a supported versioned MCP contract. Preserve capture fail-open behavior, but return structured status and errors to callers instead of swallowing them at the Dream Mode boundary. Do not depend on internal paths without a capability/version check.
- **Test scenarios:** Empty backlog; pending backlog with successful flush; flush failure; timeout; project filter mismatch; non-Git/unresolved project; bounded event/session retrieval; truncation; sanitized provenance fields preserved; version mismatch.
- **Verification:** CodeMem integration tests invoke the exact configured MCP command and assert the logical operation schemas, limits, filtering, and failure reporting. Record the source revision and version in the test fixture.

### U2. Enforce repository-root scope and boundary workflow

- **Goal:** Make Dream Mode resolve project scope and complete boundary handling before candidate mining.
- **Requirements:** R1, R2, R3, R4, R12.
- **Files:** `skills/dream-mode/SKILL.md`; supporting Dream Mode adapter or tool contract introduced by U1.
- **Approach:** Update the skill contract to distinguish immutable CodeMem records from the authorized ingestion flush. Replace the current “check status, then defer if pending” branch with explicit root resolution, flush, status capture, and visible incomplete-boundary handling. Keep normal distillation as the first candidate source.
- **Test scenarios:** Repository root with no override; conflicting global override; pending events; successful flush; failed flush; no indexed candidates after a completed boundary.
- **Verification:** Scenario fixtures assert structured result states for repository root, conflicting override, non-Git invocation, successful flush, failed flush, timeout, missing capability, and no indexed candidates after a completed boundary. No incomplete case may pass as a trustworthy empty result.

### U3. Add bounded raw-event fallback and audit output

- **Goal:** Recover reviewable candidates when CodeMem indexing is empty or insufficient without weakening safety.
- **Requirements:** R5, R6, R7, R8, R12.
- **Files:** `skills/dream-mode/SKILL.md`; fallback extraction adapter or evaluation fixtures introduced by the implementation.
- **Approach:** Use only the selected repository project and the 14-day/2,000-event bounds. Sanitize and reject sensitive or instruction-bearing content before judging. Extract at most 10 candidates with evidence IDs, dates, source labels, uncertainty, truncation state, and rejection reasons. Never write raw transcripts to Basic Memory.
- **Test scenarios:** Raw events produce a durable decision; routine events are rejected; unsupported inference is rejected; fallback candidate provenance is retained; empty fallback is auditable.
- **Verification:** Fixtures assert the event and candidate limits, sanitization, routine/unsupported-inference rejection, provenance retention, truncation handling, and auditable empty-result fields. Provisional fallback candidates cannot trigger writes.

### U4. Preserve duplicate, conflict, and approval gates

- **Goal:** Keep Basic Memory promotion deliberate and recoverable after adding the new evidence source.
- **Requirements:** R6, R8, R9, R10, R11.
- **Files:** `skills/dream-mode/SKILL.md`; existing Basic Memory tool interaction contract.
- **Approach:** Run the same read-only duplicate/conflict classification for both indexed and fallback candidates. Ask for approval immediately before each write or edit, then read back every changed note.
- **Test scenarios:** Duplicate candidate is skipped; consistent update targets the canonical note; conflict pauses for resolution; rejected proposal causes no write; approved write is verified by readback.
- **Verification:** Tool-call evaluation confirms no Basic Memory mutation during discovery, no CodeMem record edits, only the authorized flush lifecycle operation, and correct post-write verification.

## Verification Contract

| Area | Command or scenario | Pass condition |
|---|---|---|
| CodeMem boundary | CodeMem MCP integration test suite at the pinned source revision/version | Status, flush, bounds, project filtering, truncation, and failure state are structured and correct. |
| Dream Mode workflow | Indexed-candidate scenario evaluation | Normal distillation is preferred and documented candidates are excluded. |
| Dream Mode fallback | Raw-event scenario evaluation | Bounded provenance-backed proposals are produced when indexing is empty. |
| Scope safety | Conflicting override scenario | Repository-root project wins and the conflict is visible. |
| Boundary safety | Pending/failed/timeout/missing-capability scenarios | Dream Mode returns `boundary_incomplete` or `capability_unavailable`; it never reports a trustworthy empty result for incomplete evidence. |
| Basic Memory safety | Duplicate/conflict/approval scenarios | Discovery is read-only; writes occur only after explicit approval and are read back. |
| Static quality | Skill/document review plus exact CodeMem and Dream Mode fixture commands | No safety rule is dropped, each AE1-AE7 maps to a passing fixture, and the configured command/version is verified. |

## Definition of Done

- CodeMem exposes the required supported boundary and fallback evidence operations through a pinned, version-checked interface from `kunickiaj/codemem`, with the source revision recorded.
- Dream Mode derives project scope from the current Git root and reports attribution conflicts.
- Dream Mode requests the bounded ingestion flush before distillation and exposes incomplete-boundary state without editing stored CodeMem records.
- Distillation remains the preferred source, with bounded raw-event fallback when needed.
- All candidates include source and event/session provenance.
- Empty results are auditable and cannot be caused by silently ignored pending events.
- Basic Memory duplicate/conflict classification, explicit approval, and readback verification remain intact.
- All acceptance examples have passing behavioral coverage.
- No raw transcripts, secrets, or unapproved durable-memory writes are introduced.
- Capability absence, version skew, non-Git scope, truncation, timeout, and incomplete flush states have explicit tests and result schemas.
- Abandoned experimental paths and temporary diagnostic code are removed before completion.

## Sources and Research

- `skills/dream-mode/SKILL.md` — current safety boundary and workflow.
- `opencode.json` — enabled CodeMem and Basic Memory integration surfaces.
- `codemem` CLI `raw-events-status` — verified pending backlog status capability.
- Installed CLI command is `codemem db raw-events-status`; the package/plugin version mismatch must be resolved and pinned before implementation.
- Installed package metadata identifies the source repository as `https://github.com/kunickiaj/codemem`.
- `codemem` internal `flushRawEvents` and boundary-flush path — verified existing flush primitive and current best-effort failure behavior.
- `@codemem/mcp` distillation tool — verified current project resolution, judged distillation, and absence of exposed raw-event status/flush tools.

---
name: dreamer-nightly
description: Run the Baymax Codex Dreamer nightly flow when the user wants today's Dream review and pending sync JSON refreshed without performing real Graphiti or MemPalace writes.
argument-hint: "[date]"
disable-model-invocation: true
user-invocable: false
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
---

# Dreamer Nightly

## When to use

Use this for `C:\Baymax\baymax-backend` Dreamer-nightly requests such as:
- "run the existing Codex Dreamer workflow/helper"
- "update today's Dream review"
- "generate the pending dream sync json"

Do not use this for:
- real Graphiti or MemPalace write/sync execution
- Axel approval flows
- unrelated repo debugging outside the Dreamer workflow

## Inputs / context to gather

1. Confirm the target date. Default to the requested day; otherwise use the current run date.
2. Read `project_understanding/codex_dreamer_contract.py` and `project_understanding/codex_dreamer_workflow.py`.
3. Check `logs/conversation/` for same-day logs first, then older logs only if needed for context.
4. Identify the current runtime evidence files referenced by the workflow, especially `schedule_notification_runtime.py`, `main.py`, `axel_orchestrator.py`, `langgraph_state_spine.py`, and `task_reminder_intent_resolver.py`.
5. Confirm the approval boundary: unless Codex app approval is explicitly present, the sync plan must remain pending and no provider write should happen.

## Procedure

1. Inspect the Dreamer contract/workflow and any recent Dream review for the output shape.
2. Search `logs/conversation/` for `conversation-YYYY-MM-DD*` matching the target date.
3. If same-day logs exist, use them as the primary raw evidence. If they do not, fall back to older logs and mark that as a freshness downgrade.
4. Read the current runtime files needed to ground the review in live code, not just older docs.
5. Write or update:
   - `docs/dreamer/$ARGUMENTS-dream-review.md`
   - `data/dreamer/sync/$ARGUMENTS-dream-sync.json`
6. Keep the sync JSON pending unless explicit Codex approval says otherwise. Required pending fields:
   - `approval_required=true`
   - `approved_by_codex_app=false`
   - `sync_confirmation_status=pending`
   - `sync_status=pending`
7. Verify both artifact files exist and the sync JSON fields match the intended pending state.
8. Report back with:
   - Dream doc path
   - sync plan path
   - waiting-for-Codex-review yes/no
   - synced yes/no
   - warnings/errors
9. Do not `git add`, commit, or push automatically.

## Efficiency plan

1. Start with same-day log discovery before reading older logs or broad repo context.
2. Cache the small set of Dreamer contract/runtime files above instead of re-scanning the repo.
3. Stop widening the search once you have:
   - the contract/workflow shape,
   - the best available conversation evidence,
   - the runtime files that explain the current behavior,
   - verified output artifacts.
4. If no same-day logs exist, switch quickly to fallback logs plus runtime files rather than spending more time searching for missing evidence.

## Pitfalls and fixes

- Symptom: the review sounds more certain than the evidence supports.
  - Likely cause: no same-day logs or no provider-read refresh.
  - Fix: state the freshness downgrade and avoid claiming provider-backed validation.

- Symptom: docs disagree with runtime behavior.
  - Likely cause: build-control docs are stale.
  - Fix: treat live runtime files as fresher evidence and mention the drift.

- Symptom: the workflow is about to perform a real sync.
  - Likely cause: approval boundary was skipped.
  - Fix: stop and restore pending-only behavior unless explicit Codex approval is present.

## Verification checklist

- `docs/dreamer/{date}-dream-review.md` exists.
- `data/dreamer/sync/{date}-dream-sync.json` exists.
- JSON fields reflect the correct pending state unless explicit approval changed it.
- The final report explicitly says whether findings are waiting for Codex review and whether anything was synced.
- Any missing same-day logs, provider-read gaps, or docs/runtime drift are called out as warnings.
- No git publication steps were performed.

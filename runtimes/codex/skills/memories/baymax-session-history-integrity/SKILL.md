---
name: baymax-session-history-integrity
description: Repair or diagnose Baymax Conversation V2/V3 SQLiteSession history when Responses API replay has fake IDs, missing reasoning IDs, or a bounded protocol bundle split.
argument-hint: "[session-id | test-path]"
disable-model-invocation: true
user-invocable: false
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
---

# Baymax Session History Integrity

## When to use

Use in `C:\Baymax\baymax-backend` for `SQLiteSession`, `PairSafeSQLiteSession`, Responses API replay failures, `fake_id`/`__fake_id__`, missing `rs_` reasoning IDs, or history limits that may split a protocol bundle.

Do not use RuntimeStore to reconstruct history, and do not modify a persisted session unless the user authorizes the data repair.

## Inputs / context to gather

1. Read `conversation_v2/sessions.py`, especially `_repair_tool_history_suffix` and `final_model_input_guard`.
2. Identify the exact failing model-facing history items and any affected SQLite rows; distinguish canonical persisted data from audit data.
3. Check the installed SDK's reasoning-ID policy and run the focused integrity tests before broad suites.

## Procedure

1. Retrieve canonical SQLite history before applying a history limit.
2. Remove synthetic `fake_id` and `__fake_id__` values before persistence and repair legacy rows on retrieval without changing valid provider IDs.
3. Treat these as indivisible replay bundles: reasoning -> final assistant message; reasoning -> function-call(s) -> function-call-output(s); and tool-cycle -> reasoning -> final message.
4. If a limit intersects a bundle, expand backward to its complete boundary. Validate that replayed reasoning has a usable `rs_` ID.
5. Quarantine invalid reasoning. Preserve an orphan legacy assistant message as an ID-less synthetic assistant message rather than replaying its invalid `msg_` provider ID.
6. For an authorized database repair, change only proven-invalid rows/fields, preserve visible user-facing content, and verify unrelated sessions remain unchanged.

## Efficiency plan

1. Start with `test_conversation_v2_session_integrity.py`; do not begin with a broad V2 suite.
2. Inspect only the affected session's raw rows before deciding whether any persistent repair is needed.
3. Stop if canonical history is already valid; report the failing downstream boundary instead of rewriting data.

## Pitfalls and fixes

- Symptom: `msg_...` is replayed without `rs_...`.
  - Likely cause: reasoning -> message was not classified as a bundle, or the reasoning row has no replayable ID.
  - Fix: quarantine the reasoning and make the orphan message ID-less.
- Symptom: a test run is blocked by `WinError 5` at `C:\ProgramData\Axel\CodexJobs\async_jobs`.
  - Likely cause: protected default async-job storage.
  - Fix: set workspace-local `AXEL_CODEX_ASYNC_ROOT` (and `AXEL_CODEX_JOBS_ROOT` if required) before collection.
- Symptom: a broad tools suite collects no tests.
  - Likely cause: an unrelated exact tool-registry assertion mismatch.
  - Fix: report focused integrity results separately; repair/rerun the registry assertion before claiming full-suite success.

## Verification checklist

- Focused integrity coverage exercises reasoning -> message, multi-call bundles, moving boundaries, legacy message repair, and placeholder IDs.
- Valid complete `rs_`, `msg_`, and `fc_` IDs are unchanged.
- Final model-facing history contains no incomplete bundles or placeholder IDs.
- Any database repair names the exact session/rows changed and confirms unrelated sessions were not modified.

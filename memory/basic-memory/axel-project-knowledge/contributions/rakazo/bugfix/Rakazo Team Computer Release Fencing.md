---
title: Rakazo Team Computer Release Fencing
type: bugfix
permalink: axel-project-knowledge/contributions/rakazo/bugfix/rakazo-team-computer-release-fencing
repository: rakazo
upstream: https://github.com/elie222/rakazo.git
issue: 77
pull_request: 78
branch: main
contribution_type: bugfix
category: team-computer-control
base_commit: 3c6e209c8bc3ccd29fd0ff2551ff534b5928b4d6
commit: 2eaf49304c830edf25c3fcaeb49489227aef533b
base_sha: 3c6e209c8bc3ccd29fd0ff2551ff534b5928b4d6
head_sha: 2eaf49304c830edf25c3fcaeb49489227aef533b
publication_status: draft_pr_open
---

## Problem

A stale `computer.release` request from one Team bot could clear another Team bot's newer active takeover on the shared Team Computer.

## Symptoms

The release handler identified the current owner for event and waiting-run lookup but still revoked the provider using the requester and unconditionally reset the shared Computer control fields. A stale Writer release therefore changed Researcher's active lease to bot control, cleared the lease, published a release event, and could resume a waiting Researcher run.

## What Didn't Work

An initial implementation added an intermediate `controlHolder: "none"` claim before finalization. Independent review found that a cancellation or event-finalization failure could leave the lease fenced in `none` state, causing a retry to no-op. The claim was removed after making expiry job keys lease-specific.

## Root Cause

Manual release was not owner-scoped or lease-fenced. It used the shared Computer row without requiring the requester to equal `controlBotId`, and it bypassed `finalizeComputerControlRelease`, which is the atomic lease-and-event fence.

## Solution

The manual release path now:

- Requires an active user takeover whose `controlBotId` equals the requesting bot.
- Captures the exact current `controlLeaseId` and passes the actual owner to provider screen revocation.
- Calls `deps.events.finalizeComputerControlRelease` with the captured lease and `holder: "bot"`; a failed fence is an idempotent no-op.
- Publishes the release event and resumes the newest waiting takeover only after fenced finalization succeeds.
- Uses lease-specific computer expiry job keys, so post-finalization cancellation cannot cancel a replacement takeover's expiry job.

Automatic expiry logic in `packages/adapters/src/computer-control.ts` remains unchanged and continues to fence by lease ID.

## Tests and Verification

- Before the production fix, `4d: a stale Team release cannot clear a newer bot takeover` failed at `packages/testkit/src/journeys.test.ts` with `expected 'bot' to be 'user'` after the stale release.
- After the fix, the focused journey passed and covered shared Team ownership, newer Researcher lease survival, unchanged expiry and owner, no stale release event, no unintended waiting-run resume, normal Researcher release, one release event with the matching lease ID, and repeated-release idempotency.
- API tests passed: 4 files, 14 tests.
- Computer-control, expiry-key, and reconciler tests passed: 3 files, 15 tests.
- `pnpm check` passed: 19 Turbo tasks.
- `pnpm exec biome lint .` and `pnpm exec biome check --formatter-enabled=false .` passed; touched files also pass normal Biome check.
- `git diff --check HEAD` passed.
- The full journeys file reached 20 passing tests; one unrelated Windows Fake home-store test failed with an `EPERM` rename error.
- Issue [#77](https://github.com/elie222/rakazo/issues/77) was created and draft PR [#78](https://github.com/elie222/rakazo/pull/78) was opened from `bferanmi806-sketch:fix/stale-team-takeover-release`.
- Commit: `2eaf49304c830edf25c3fcaeb49489227aef533b`; base SHA: `3c6e209c8bc3ccd29fd0ff2551ff534b5928b4d6`; PR remains draft and not ready for review.

## Prevention

For shared control state, authorization must use both the current owner and the current lease fence. Cleanup helpers that atomically clear state and append the corresponding event should be reused by every manual path. Any job cancellation performed after state finalization must be keyed by the exact lease, not only the shared resource, or a concurrent replacement can lose its expiry job.

## Related Issues or PRs

- Issue: https://github.com/elie222/rakazo/issues/77
- Draft PR: https://github.com/elie222/rakazo/pull/78
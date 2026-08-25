---
title: 'OpenMausBot #358 Interrupt Dispatch Race'
type: contribution
permalink: axel-project-knowledge/contributions/openmausbot/bugfix/open-maus-bot-358-interrupt-dispatch-race
repository: OpenMausBot
upstream: https://github.com/milind-soni/OpenMausBot
issue: null
pull_request: 358
branch: feat/existing-vm-254
contribution_type: bugfix
category: bugfix
---

# OpenMausBot #358 Interrupt Dispatch Race

## Problem
The Existing VM integration test intermittently left a bot busy after `POST /api/bots/:id/interrupt` during the full cross-platform test floor.

## Symptoms
macOS and Ubuntu CI failed at `server/index.test.ts:1376`, where the bot was expected to become idle after interruption. Windows passed. The focused API test could pass locally, masking the race.

## What Didn't Work
Changing only the mode-change error wording fixed the first CI assertion but exposed the independent interrupt failure. Increasing the polling timeout would not address an interrupt that was lost before provider registration.

## Root Cause
`startTurn` marked the bot busy before the asynchronous provider dispatch registered its active turn. The interrupt route could run in that interval; provider adapters correctly returned without stopping anything because no active turn existed yet. The later provider process then started and remained hung.

## Solution
Added a server-level pending-interrupt set and a shared `interruptProviderTurn` helper. Active interrupt paths queue the thread before asking the adapter to stop. After `sendTurn` registers the provider turn, `startTurn` replays any queued interrupt. Pending state is cleared on completion, dispatch failure, or a new thread boundary. The same handoff is used for bot, room, watchdog, routine, and lifecycle interruption paths.

## Why This Works
The interrupt request is retained across the only unsafe interval, while adapters still handle normal active-turn cancellation. Completion and failure cleanup prevent stale requests from affecting later turns.

## Tests and Verification
- Focused API suite: 75 passed, 1 skipped.
- Full local suite: 1470 passed, 75 skipped across 148 files.
- `pnpm typecheck` passed.
- Broker, updater, desktop-viewer, and packaged-server checks passed.
- CI run `32588107948` passed macOS, Ubuntu, Windows, package/smoke, and Swift/iOS jobs.
- CodeRabbit passed.
- Follow-up commits `123c7ef` and `745d33a` were pushed to PR #358.
- PR #358 is open and non-draft as of 2026-08-22.

## Prevention
When a request makes state visible before asynchronous ownership is established, cancellation must be represented independently of the downstream adapter's active registry. Test cancellation immediately after the visible busy transition and run that test under the repository's concurrent test floor.

## Related Issues or PRs
- Pull request: https://github.com/milind-soni/OpenMausBot/pull/358
- Relevant implementation: `server/index.ts`
- CI run: https://github.com/milind-soni/OpenMausBot/actions/runs/32588107948
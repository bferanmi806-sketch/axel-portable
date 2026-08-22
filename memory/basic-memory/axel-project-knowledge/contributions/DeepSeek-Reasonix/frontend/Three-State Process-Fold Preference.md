---
title: Three-State Process-Fold Preference
type: contribution
permalink: axel-project-knowledge/contributions/deep-seek-reasonix/frontend/three-state-process-fold-preference
repository: DeepSeek-Reasonix
upstream: https://github.com/esengine/DeepSeek-Reasonix
issue: 7265
pull_request: 7518
branch: feat/7265-three-state-process-fold
contribution_type: feature
category: frontend
tags:
- contribution
- frontend
- streaming-ui
- process-fold
---

## Problem
Issue #7265 reported that the desktop work-process/reasoning fold was forcibly expanded while a turn streamed, so the existing preference did not provide a way to keep the fold closed during generation.

## Symptoms
Live reasoning remained visible and scrolling during streaming, which was distracting and could contribute to transcript rendering lag for chatty reasoning models. The current call site was `desktop/frontend/src/components/Transcript.tsx`, not the older `Message.tsx` example in the issue description.

## What Did Not Work
The existing `auto` and `expanded` preference states were insufficient because the `TurnCollapse` running-work effect unconditionally called `setOpen(true)`. Changing only the initial default would still allow that lifecycle effect to override a collapsed choice. A separate legacy `expand_thinking` bridge setting was not changed because the current process-fold path uses the frontend local-storage preference instead.

## Root Cause
`TurnCollapse` treated `hasRunningWork` as an unconditional open command. The preference was applied after completion, but not to the streaming transition or to live preference changes for folds already mounted.

## Solution
The frontend preference now has three validated values in `processFoldPreference.ts`:

- `auto`: open while work is active, then close after completion when the turn has visible content outside the fold.
- `collapsed`: stay closed while streaming and after completion when outside content exists.
- `expanded`: stay open through streaming and completion.

`Transcript.tsx` centralizes the decision in `shouldOpenProcessFold` and applies it during initial render, running/completed transitions, and preference-event updates. Existing manual overrides remain supported. Completed folds with no rendered content outside the fold remain open and cannot be manually hidden, preventing an apparently empty response. The settings segmented control and English, Simplified Chinese, and Traditional Chinese labels expose all three states.

## Why This Works
The lifecycle effect no longer overrides `collapsed` while work is active, while `auto` and `expanded` retain their prior behavior. The safety predicate takes precedence only after work has completed and the fold is the turn's only visible content. Live preference changes clear per-fold overrides and immediately reconcile mounted folds.

## Tests and Verification
The following changed-area checks passed after rebasing onto the latest `main-v2`:

- `transcript-process-fold.test.ts`: 34 passed.
- `transcript-fold-preference.test.tsx`: 13 passed, including streaming, completion, live changes, stable stream-to-completion identity, and process-only safety.
- `settings-refresh-snapshot.test.tsx`: 84 passed.
- Frontend TypeScript typecheck, hook lint, CSS/z-index checks, Vite production build, and bundle-budget checks passed.
- GitHub PR guard checks (`cache-impact`, `docs-impact`, and label checks) passed after the required documentation-impact declaration was added.

The direct package frontend test command was also attempted. It failed at `desktop/frontend/src/__tests__/pending-prompt-stale-status.test.tsx:559` with `expected "plan-zombie", got undefined`; the same failure reproduced in isolation. It appears unrelated because the test does not directly import the changed modules, but baseline status was not independently proven against the base commit.

At record time, commit `e4102aa8` was pushed on branch `feat/7265-three-state-process-fold` to open PR [#7518](https://github.com/esengine/DeepSeek-Reasonix/pull/7518), based on `main-v2`. Issue [#7265](https://github.com/esengine/DeepSeek-Reasonix/issues/7265) remained open and the PR was unmerged.

## Prevention
For streaming UI preferences, test the full lifecycle rather than only the initial render: active streaming, completion, live preference changes, stable component identity across transitions, manual interaction, and safety invariants. Keep the state policy centralized so a running-work effect cannot silently override a user preference.

## Related Issues or PRs
- Issue: https://github.com/esengine/DeepSeek-Reasonix/issues/7265
- Pull request: https://github.com/esengine/DeepSeek-Reasonix/pull/7518
- Preference storage: `desktop/frontend/src/lib/processFoldPreference.ts`
- Fold lifecycle: `desktop/frontend/src/components/Transcript.tsx`
- Regression tests: `desktop/frontend/src/__tests__/transcript-process-fold.test.ts` and `desktop/frontend/src/__tests__/transcript-fold-preference.test.tsx`
---
title: 'OpenPets #114 Waiting Animation Duration'
type: contribution
permalink: axel-project-knowledge/contributions/openpets/feature/open-pets-114-waiting-animation-duration
repository: alvinunreal/openpets
upstream: https://github.com/alvinunreal/openpets
issue: 114
pull_request: 116
branch: feat/114-waiting-animation-speed
contribution_type: feature
category: feature
---

# OpenPets #114 Waiting Animation Duration

## Problem
The waiting sprite cycle needed a maintainer-controlled global speed setting while preserving the existing 1010 ms default.

## What Didn't Work
The full repository gates could not complete in this Windows environment. `pnpm check` and root `pnpm test` stop at the pre-existing `packages/cursor` symlink contract because symlink creation is not permitted. The desktop runner separately stops compiling the pre-existing duplicate declarations in `apps/desktop/tests/lan-state.test.ts` on upstream `main`.

## Solution
Added persisted `waitingAnimationDurationMs` state with strict Normal (1010 ms) and Relaxed (2200 ms) normalization and live IPC patch validation. Control Center Settings → Reactions exposes the localized choice and refreshes the reaction preview after saving. Built-in and installed pet renderers derive fresh sprite state tables, changing only `waiting.durationMs`; cache identity includes the configured duration so open windows reload CSS. Default and agent pet windows refresh on preference changes. Plugin sprite override/FPS paths remain separate.

## Tests and Verification
- Base upstream/main SHA: `f0f488685cd750751c4bbc42de6ccda1172c8187`.
- Focused mapping and preference-patch tests pass, including normalization, accepted values, rejected values/types, preserved mappings/durations, canonical metadata immutability, and cache identity.
- Desktop typecheck passes.
- Desktop build passes.
- `git diff --check` passes.
- Commit: `55e94614bcce36ba54368e865fda827baf66c524`.
- Draft PR: https://github.com/alvinunreal/openpets/pull/116.
- Manual GUI timing/restart verification was not available in this session.

## Prevention
For future Windows validation, enable a permitted symlink workflow or run the repository-wide gates in CI/Linux. Keep preference state normalization and render-state derivation pure so persisted-value and canonical-metadata regressions remain cheap to test.

## Related Issues or PRs
- Issue: https://github.com/alvinunreal/openpets/issues/114
- Draft PR: https://github.com/alvinunreal/openpets/pull/116

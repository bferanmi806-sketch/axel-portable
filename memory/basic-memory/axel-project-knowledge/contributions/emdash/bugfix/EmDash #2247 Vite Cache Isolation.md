---
title: 'EmDash #2247 Vite Cache Isolation'
type: contribution
permalink: axel-project-knowledge/contributions/emdash/bugfix/em-dash-2247-vite-cache-isolation
repository: emdash
upstream: https://github.com/emdash-cms/emdash
issue: 2247
pull_request: null
branch: fix/2247-vite-cache-isolation
contribution_type: bugfix
category: integration-testing
---

# EmDash #2247 Vite Cache Isolation

## Problem
Concurrent EmDash integration servers borrowed the same `demos/simple/node_modules` through symlinks. Vite therefore resolved both servers' dependency-optimizer cache paths to the same physical directory, making the test harness unsafe for parallel server startup.

## Symptoms
The new concurrent-server regression produced identical Vite cache realpaths for two otherwise distinct fixture roots. On the unmodified base, the repro also hit the Astro shared-lock failure: `AssertionError: expected ... not to be ...` at the cache-path assertion, with the original concurrent startup path timing out when roots were shared.

## What Did Not Work
Native Windows could not run the official integration global setup because `execFile("pnpm", ...)` returned `spawn pnpm ENOENT`, and the harness also hit Windows symlink-permission errors. Validation used a Linux/WSL checkout instead. The default 30-second Vitest test timeout was also too short for a cold WSL run: the corrected test completed in 38.3 seconds, so the regression now declares a 120-second timeout locally rather than changing the global timeout.

## Root Cause
The fixture root was isolated, but its symlinked `node_modules` pointed Vite's cache resolution back to shared physical paths. The test harness had no per-server Vite `cacheDir` override.

## Solution
`createTestServer()` now derives `.vite-cache` inside each generated server workdir and passes it through the test-only `EMDASH_TEST_VITE_CACHE` environment variable. The fixture's Astro config maps that variable to Vite's `cacheDir`, falling back to Vite's default when the variable is absent. The concurrent regression resolves both cache paths with `realpathSync()` and asserts they differ, while retaining the existing seeded-content checks. The test has a 120-second timeout to cover cold integration startup.

## Why This Works
Each server has a unique physical cache directory even though dependencies remain shared through the existing symlink. Vite's optimizer no longer shares cache state across concurrent fixture roots, while normal fixture execution remains unchanged when the test-only variable is not set.

## Tests and Verification
- RED proof was reproduced on unmodified `main` before the fix.
- Concurrent-server race test passed 5/5 in the focused loop.
- Final WSL run passed: 1 test, 1 passed, 38.3 seconds, using the official integration config with the 120-second timeout.
- Core integration/CLI coverage passed: 8 files, 90 tests.
- `pnpm build` passed.
- `pnpm --filter emdash typecheck` passed.
- `pnpm lint:quick` passed with 0 diagnostics across 2,303 files.
- Focused Oxfmt and Prettier checks passed; `git diff --check` passed.
- Codex adversarial review found no substantive blocking issue. It noted only non-blocking residual risks around explicit teardown assertions, the pre-existing process-kill cleanup path, and caller overrides of the test-only environment variable.
- The working tree contains only the three intended modified files; no commit, PR, or GitHub write was made.

## Prevention
When an integration harness shares dependencies through symlinks, isolate tool caches explicitly at the same boundary as the server workdir. Regression tests that boot real servers should set a timeout consistent with cold startup plus setup and should assert the physical resource isolation, not only distinct logical paths. Keep unrelated Windows and repository-wide baseline failures documented rather than masking them in the patch.

## Related Issues or PRs
- Issue: https://github.com/emdash-cms/emdash/issues/2247
- Historical related issue: #1604
- Pull request: none at record time
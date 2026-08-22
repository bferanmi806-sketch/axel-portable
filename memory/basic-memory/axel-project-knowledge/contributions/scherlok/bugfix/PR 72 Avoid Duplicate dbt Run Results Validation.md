---
title: PR 72 Avoid Duplicate dbt Run Results Validation
type: bugfix
permalink: axel-project-knowledge/contributions/scherlok/bugfix/pr-72-avoid-duplicate-dbt-run-results-validation
repository: scherlok
upstream: https://github.com/rbmuller/scherlok
issue: null
pull_request: 72
branch: feat/69-dbt-run-results
contribution_type: bugfix
category: bugfix
---

# Problem

PR #72's `dbt-run-and-watch` wrapper loaded and structurally validated `target/run_results.json`, then passed the same mapping to `successful_model_unique_ids()`, which structurally validated it again.

## Symptoms

The behavior was correct, but the same parsed artifact incurred redundant structural validation on the normal CLI path.

## What Didn't Work

Removing validation from `load_run_results()` would reduce the duplicate work but weaken the loader's existing validation contract. Replacing the artifact with a cached or mutable wrapper would add unnecessary API and mutation complexity for a small orchestration issue.

## Root Cause

The public `successful_model_unique_ids()` API accepts untrusted arbitrary objects and must retain its own validation. The CLI wrapper already has a validated artifact after `load_run_results()`, but it used the public boundary function again instead of filtering the validated result rows directly.

## Solution

Extracted `_successful_model_unique_ids_from_validated()` for the filtering-only operation. `successful_model_unique_ids()` still validates arbitrary input before delegating, while `dbt-run-and-watch` calls the filtering helper only after `load_run_results()` has validated the artifact.

## Why This Works

The loader's validation and all existing exceptions remain unchanged. The public helper's validation behavior remains unchanged. On the wrapper's normal path, `_validated_results()` is invoked once and the resulting rows are reused for ID filtering.

## Tests and Verification

- `uv run --extra dev python -m pytest --basetemp C:\\Users\\bfera\\AppData\\Local\\Temp\\opencode\\scherlok-pytest tests/test_dbt_run_results.py tests/test_dbt_run_and_watch.py` passed: 30 tests.
- `uv run --extra dev ruff check .` passed.
- `git diff --check` passed.
- A regression assertion verifies one structural-validation call on the profiling path.
- Commit `b67f83e` was pushed to `bferanmi806-sketch/scherlok:feat/69-dbt-run-results`.
- PR #72 remains open and approved; remote Python 3.10 and 3.12 checks were pending at verification time.

## Prevention

Keep validation at untrusted-input boundaries, then use an explicitly named filtering or transformation helper for callers that already hold validated data. Add a call-count regression assertion when a maintainer flags redundant validation in an orchestration path.

## Related Issues or PRs

- PR: https://github.com/rbmuller/scherlok/pull/72
- Upstream repository: https://github.com/rbmuller/scherlok

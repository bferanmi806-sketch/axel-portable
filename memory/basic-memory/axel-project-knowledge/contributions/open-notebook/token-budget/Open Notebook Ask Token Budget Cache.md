---
title: Open Notebook Ask Token Budget Cache
type: report
permalink: axel-project-knowledge/contributions/open-notebook/token-budget/open-notebook-ask-token-budget-cache
repository: open-notebook
upstream: https://github.com/lfnovo/open-notebook
issue: null
pull_request: 1247
branch: fix/1221-ask-output-token-budget
contribution_type: bugfix
category: token-budget
---

## Problem
Ask resolves `OPEN_NOTEBOOK_ASK_MAX_TOKENS` separately in every fan-out `provide_answer` node and again in `write_final_answer`.

## Symptoms
The repeated reads caused unnecessary environment parsing, could repeat fallback warnings, and allowed theoretical inconsistency if the environment changed during one Ask run.

## What Didn't Work
No alternate implementation was needed; the existing helper was correct but uncached.

## Root Cause
`get_ask_max_tokens()` read the process environment on every call instead of treating the configured budget as process-level configuration.

## Solution
Decorated `get_ask_max_tokens()` with `functools.cache`. The no-argument cache stores every resolved outcome, including the default and invalid-value fallbacks, without adding graph state or changing the API.

## Why This Works
All nodes in one API process reuse the first resolved value, so parsing and warnings happen once and the budget is consistent for an Ask run. Changing the environment still requires restarting the API process/container, matching the existing documentation.

## Tests and Verification
- Added an autouse test fixture that clears the cache before and after every token-budget test.
- Added a focused test proving an environment change does not affect the cached value and that `cache_clear()` makes a new value visible.
- `uv run pytest tests/test_ask_token_budget.py -v`: 9 passed.
- `uv run ruff check open_notebook/graphs/ask.py tests/test_ask_token_budget.py`: passed.
- `uv run ruff format --check open_notebook/graphs/ask.py tests/test_ask_token_budget.py`: passed.
- `uv run python -m mypy .`: no issues in 131 source files.
- `git diff --check`: passed.
- Follow-up commit `6dcfc993743d74ed6359959107be718bcdf29cf8` was pushed to the existing branch. PR #1247 remains open and the review thread was not changed.

## Prevention
Cache process-level environment configuration at its resolver boundary, and explicitly clear such caches in tests to prevent state leakage.

## Related Issues or PRs
- Pull request: https://github.com/lfnovo/open-notebook/pull/1247
- Upstream repository: https://github.com/lfnovo/open-notebook

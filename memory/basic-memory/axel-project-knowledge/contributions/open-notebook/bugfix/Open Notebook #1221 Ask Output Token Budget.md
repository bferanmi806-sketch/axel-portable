---
title: 'Open Notebook #1221 Ask Output Token Budget'
type: report
permalink: axel-project-knowledge/contributions/open-notebook/bugfix/open-notebook-1221-ask-output-token-budget
repository: lfnovo/open-notebook
upstream: https://github.com/lfnovo/open-notebook
issue: 1221
pull_request: 1247
branch: fix/1221-ask-output-token-budget
contribution_type: bugfix
category: bugfix
tags:
- open-notebook
- bugfix
- github-contribution
- ask
---

# Open Notebook #1221 Ask Output Token Budget

## Problem

Open Notebook Ask/Q&A passed `max_tokens=2000` to structured strategy generation, intermediate result extraction, and final answer synthesis. The shared small budget silently truncated prose answers, especially for token-dense languages and reasoning models.

## Symptoms

The strategy step and both prose steps had different output requirements but used the same hardcoded budget. A strategy is bounded by at most five searches, while intermediate and final answers can require substantially more output.

## What Didn't Work

Running the repository-wide suite on Windows did not produce a green baseline: unrelated Windows path, proxy-environment, and pytest temp-directory permission failures remained. Representative failures reproduced on a clean checkout of upstream `main`, so no unrelated repository changes were made to mask them.

## Root Cause

`open_notebook/graphs/ask.py` used the literal `max_tokens=2000` at all three model provisioning call sites and had no process configuration for the prose stages.

## Solution

Added `DEFAULT_ASK_MAX_TOKENS = 8192`, `ASK_STRATEGY_MAX_TOKENS = 2000`, and `OPEN_NOTEBOOK_ASK_MAX_TOKENS` with a testable `get_ask_max_tokens()` helper. Positive integer overrides are accepted; unset, malformed, zero, negative, and blank explicit values fall back to 8192 with a warning. Strategy generation uses the fixed strategy constant, while intermediate and final answer nodes read the configurable budget at invocation time. Added focused tests, `.env.example` guidance, environment-reference documentation, and an unreleased changelog entry.

## Why This Works

The bounded structured strategy remains deliberately constrained, while the two prose-producing stages receive a larger configurable budget through the existing per-call Esperanto configuration path. No API request field, model selection, prompt, response parsing, provider branch, frontend behavior, database schema, or dependency changed.

## Tests and Verification

- `uv sync` completed successfully.
- Focused Ask tests: `uv run pytest tests/test_ask_token_budget.py -v` -> 8 passed, 1 dependency deprecation warning.
- Full backend suite: `uv run pytest tests/ -v` -> 611 passed, 3 failed, 49 errors, 2 dependency warnings. The same representative failures reproduced on clean upstream `a7de90d38aaf18ee85fd661854d35c11e44613e2`.
- `uv run ruff check .` passed.
- `uv run ruff format --check open_notebook/graphs/ask.py tests/test_ask_token_budget.py` reported both files already formatted.
- `uv run python -m mypy .` reported no issues in 131 source files.
- `git diff --check` passed.
- Manual review found no correctness, security, architecture, performance, or scope blockers.
- Commit `7ca621f13a9ed000b43e6ff656587a68e9f4f894` was pushed to `bferanmi806-sketch/open-notebook`.
- Draft PR #1247 is open against `lfnovo/open-notebook:main`; GitHub currently reports `MERGEABLE`, merge state `BLOCKED` because it is a draft, with no checks reported yet. No reviewers were requested.

## Prevention

When multiple model calls serve different output roles, keep their budgets explicit and independently configurable rather than sharing a literal. For repository-wide validation on Windows, compare representative failures with a clean upstream worktree before attributing them to a focused backend change.

## Related Issues or PRs

- Issue: https://github.com/lfnovo/open-notebook/issues/1221
- Draft PR: https://github.com/lfnovo/open-notebook/pull/1247
- Source module: `open_notebook/graphs/ask.py`

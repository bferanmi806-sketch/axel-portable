---
name: baymax-graphify
description: Diagnose, query, or rebuild Graphify in C:\Baymax\baymax-backend when the global command fails, the graph is empty, or the user asks to verify graph health.
argument-hint: "[query | rebuild]"
disable-model-invocation: true
user-invocable: false
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
---

# Baymax Graphify

## When to use

Use for Graphify orientation, repair, rebuilding, or integrity checks in `C:\Baymax\baymax-backend`.

Do not use this as a reason to rebuild a healthy graph for ordinary targeted code inspection.

## Inputs / context to gather

1. Check `graphify-out\graph.json`, `manifest.json`, and `GRAPH_REPORT.md`.
2. Run Graphify from the repository virtual environment, not PATH.
3. For a rebuild, preserve the existing graph artifacts until the replacement passes integrity checks.

## Procedure

1. Query with `\.venv\Scripts\graphify.exe` or `\.venv\Scripts\python.exe -m graphify`; if the existing graph is healthy, query it directly rather than rebuilding.
2. If the global executable reports `uv trampoline failed to canonicalize script path`, switch to the repo-local executable immediately.
3. If rebuilding, upgrade Graphify first: `\.venv\Scripts\python.exe -m pip install --upgrade graphifyy`.
4. Run a full code-only extraction: `\.venv\Scripts\python.exe -m graphify extract . --code-only --max-workers 4`.
5. Diagnose the result: `\.venv\Scripts\python.exe -m graphify diagnose multigraph --graph graphify-out\graph.json`.
6. Refresh clustering/reporting without visualization for large graphs: `cluster-only . --no-viz --no-label`.
7. Run one focused query relevant to the work and report graph health plus scan exclusions.

## Efficiency plan

1. Reuse a healthy `graphify-out\graph.json`; do not rebuild for routine orientation.
2. If the repo-local executable also cannot query, fall back to targeted `rg` and direct reads instead of repeatedly retrying Graphify.
3. Skip HTML visualization by default for large graphs.

## Pitfalls and fixes

- Symptom: PATH `graphify.exe` cannot canonicalize its script path.
  - Cause: the global launcher is broken in this checkout.
  - Fix: use the repository `.venv` executable or module invocation.
- Symptom: rebuild reports nearly no graph edges or marks sources deleted.
  - Cause: Graphify 0.9.15 misdetected this Python checkout.
  - Fix: restore the prior graph, upgrade to graphifyy 0.9.17 or later, then do a full rebuild.
- Symptom: custom parallel extraction fails on Windows spawn/entry-point rules.
  - Cause: multiprocessing script lacks a guarded entry point.
  - Fix: use the CLI or rerun extraction with `parallel=False`.

## Verification checklist

- `graphify-out\graph.json` and `manifest.json` exist.
- Diagnostics show no missing or dangling endpoints.
- A focused repo query succeeds.
- Any inaccessible `.pytest-temp` scan warning is reported as excluded coverage.

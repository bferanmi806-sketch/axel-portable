---
description: Run the local memory package verification and dependency audit.
agent: local-memory-build
---

Run the project's deterministic verification path:

1. `npm run verify`
2. `npm audit --omit=dev`
3. Inspect `git status --short` and report unrelated pre-existing changes.

Do not modify global OpenCode configuration or enable the plugin. Report exact
pass/fail output and any limitation.

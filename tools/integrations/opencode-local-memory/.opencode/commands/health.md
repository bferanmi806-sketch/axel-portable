---
description: Check the focused project and OpenCode integration health without changing state.
agent: local-memory-reviewer
---

Run read-only checks for this project and the host integration:

1. `npm run verify`
2. `npm audit --omit=dev`
3. `opencode debug config`
4. `opencode mcp list`
5. Confirm the live global configuration does not contain this local plugin.

Do not enable the plugin or modify any configuration. Report failures and
whether they are package-local, OpenCode-global, or external-service issues.

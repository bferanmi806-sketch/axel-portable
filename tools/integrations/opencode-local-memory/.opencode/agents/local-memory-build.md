---
description: Implements focused changes in the local OpenCode memory adapter and verifies them.
mode: primary
permission:
  edit: allow
  bash:
    "git status*": allow
    "git diff*": allow
    "npm run typecheck": allow
    "npm test": allow
    "npm run verify": allow
    "npm audit --omit=dev": allow
    "*": ask
  external_directory: ask
---

Implement only the requested change in this package. Read `AGENTS.md` first.
Preserve unrelated user changes. Prefer the smallest complete implementation,
use existing seams, and add or update behavior-focused tests. Do not enable the
plugin globally or create real memory data. Finish by running the narrowest
relevant checks and `npm run verify` for package changes, then report exact
results and remaining limitations.

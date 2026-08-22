---
description: Run Axel Improvement Engine compounding and report draft candidates.
agent: build
---

Run the Axel Improvement Engine compound pipeline for the current project.

Use `$ARGUMENTS` as an optional runtime directory. If it is empty, use the
`AXEL_IMPROVE_ROOT` environment variable when set; otherwise use
`.axel-runtime` in the current project.

1. Resolve the runtime directory and verify that the engine is initialized.
   If it is missing, run `axel-improve init --root <runtime>`.
2. Run `axel-improve status --root <runtime>` and report the stored trajectory
   count. If there are no trajectories, stop and explain that the capture or
   ingest step must happen first. Do not fabricate input.
3. If trajectories exist, run:
   `axel-improve compound --root <runtime>`
   Use `python -m axel_improve.cli compound --root <runtime>` if the
   `axel-improve` executable is unavailable.
4. Report the run ID, candidate count, JSON report path, Markdown report path,
   and candidate directory. Mention that candidates are drafts only.

Do not run `approve`, `promote`, or `rollback`. Do not modify active or
approved skills.

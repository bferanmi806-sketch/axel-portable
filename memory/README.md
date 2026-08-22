# Memory Sources

Basic Memory's human-readable Markdown is the canonical portable memory. The
SQLite database, WAL files, embedding cache and service logs are derived or
runtime state and are intentionally excluded.

## Projects

- `basic-memory/axel/` is the current configured `Axel` project from `C:\Users\bfera\Axel-memory\Axel`.
- `basic-memory/main/` is the current configured `main` project from `C:\Users\bfera\basic-memory`.
- `basic-memory/axel-project-knowledge/` is the current configured `axel-project-knowledge` project from the Paseo/Codex test workspace.
- `axel-worktree/` is a separate, newer working copy found in `paseo-codex-test\Axel`. It contains uncommitted changes and is preserved for review, not automatically merged.
- `axel-project/` is the active Markdown knowledge map under `Documents\Axel Workspaces\axel_project`.

The source precedence is deliberate: restore `basic-memory/axel/` as the
runtime `Axel` project first, then reconcile `axel-worktree/` if its newer notes
are wanted. `axel-project/` is project architecture knowledge, not a replacement
for the personal profile.

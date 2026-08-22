# Ticket 01 Threat Model

## Trust Boundaries

- OpenCode hook payloads enter the adapter as untrusted runtime data.
- Plugin tuple options enter as untrusted local configuration.
- The commit-pinned upstream repository is third-party code and is not executable in this baseline.
- Future memory and model adapters cross additional boundaries but are disabled in this ticket.

## Assets

- Normal OpenCode availability.
- Future local memory contents and storage paths.
- Separation between user sessions and internal consolidation sessions.
- Integrity of global OpenCode configuration.

## Baseline Controls

- Capture, consolidation, and injection default off and fail closed on invalid switch values.
- Port failures are swallowed and reported without blocking normal OpenCode behavior.
- Unsupported event types are ignored rather than persisted.
- Internal sessions are excluded by reserved prefix or explicit registry membership.
- A custom data directory must be absolute; path resolution has no file-system side effects.
- Dependencies are exact-pinned and installed with lifecycle scripts disabled.
- The similarly named npm package is excluded because its published graph differs from the reviewed source and currently reports high-severity advisories.
- The live global OpenCode configuration is not modified during baseline development.

## Capture Controls

- The ledger accepts only `SanitizedCapture` records; hook payloads are normalized and redacted before hashing or persistence.
- Workspace, tool, and path exclusions stop records before payload serialization.
- Credential/header/private-key/configured-pattern redaction stores category metadata but not removed values.
- Text bodies are bounded; binary and oversized payload bodies are never stored.
- SQLite statements bind values rather than interpolating untrusted data.
- Transactions, WAL mode, busy timeout, per-session ordering, duplicate IDs, and integrity checks protect capture consistency.
- A newer schema is refused rather than written by older code.
- References must resolve to regular files below the allowed local references root.
- Project identity uses normalized paths and Git common directories; matching remotes are advisory only, so separate clones cannot silently share memory.
- Move reconciliation requires an explicit project ID and rejects a path already assigned to another project.
- Consolidation receives only persisted sanitized text within a strict input budget; it never receives raw hook payloads, binary bodies, or oversized bodies.
- Historical event text is explicitly treated as untrusted evidence. The internal model session has tools disabled, and model output is schema-validated before database writes.
- Internal consolidation sessions are marked by title and ID so their messages, tools, and lifecycle events are excluded from ordinary capture.
- Failed model operations retain generic durable retry state, not raw model output or exception text.

## Deferred To Later Tickets

- Secret redaction and content/path exclusions before persistence.
- Broader cross-process writer coordination, persistent read-only degraded mode, and backup/restore.
- A live isolated provider smoke test before enabling consolidation in the global configuration.
- Prompt-injection-resistant consolidation and typed model output validation.
- Reference-path allowlisting, explicit deletion, backups, and restore.

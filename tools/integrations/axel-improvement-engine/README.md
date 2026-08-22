# Axel Improvement Engine

The local SQLite ledger stores sanitized, versioned trajectory records. It has
no model calls and keeps host capture opt-in and local-only.

## Install

```text
python -m pip install -e .
```

## Initialize and ingest

```text
axel-improve init --root ./runtime
axel-improve ingest --root ./runtime --input ./fixtures/reconstructed.jsonl
axel-improve status --root ./runtime
axel-improve export --root ./runtime
axel-improve diagnose --root ./runtime
axel-improve diagnoses --root ./runtime --unresolved
axel-improve replay-build --root ./runtime --seed 11
axel-improve replay-inspect --suite ./runtime/data/replay/suite-<ID>.json
axel-improve replay-run --suite ./runtime/data/replay/suite-<ID>.json
axel-improve evaluate --root ./runtime --suite ./runtime/data/replay/suite-<ID>.json \
  --candidate-id candidate-004 --baseline-digest <BASELINE_DIGEST> \
  --candidate-digest <CANDIDATE_DIGEST> \
  --candidate-asset ./runtime/skills/candidates/candidate-004 \
  --candidate-results ./candidate-results.json \
  --checkpoint ./runtime/data/evaluations/candidate-004.checkpoint.json
axel-improve approve --root ./runtime --candidate-id candidate-004 \
  --candidate-digest <CANDIDATE_DIGEST> --evaluation ./evaluation.json \
  --operator <OPERATOR> --reason "reviewed exact diff and evaluation"
axel-improve promote --root ./runtime --candidate-id candidate-004 \
  --evaluation ./evaluation.json --approval-digest <APPROVAL_DIGEST> --repo-root .
axel-improve rollback --root ./runtime --candidate-id candidate-004 \
  --repo-root . --operator <OPERATOR> --reason "regression observed"
axel-improve retrieve --root ./runtime --query "review deployment safety" \
  --task-id task-004 --max-items 5 --max-tokens 1200 --output ./retrieved-context.json
axel-improve compound --root ./runtime --seed 21
python scripts/ticket9_demo.py --root ./ticket9-runtime
```

The ledger stores bounded, redacted records. It does not store credentials,
full raw tool output, or active skill changes. Diagnosis is deterministic-first;
it stores recurring evidence groups separately from trajectory records and keeps
one-off or unknown-target cases non-promotable.

Replay suites are sanitized, content-addressed JSON artifacts. Historical cases
are grouped by task and session before seeded development/held-out assignment.
The default fixture runner performs no subprocess or network work; budget
violations and runner failures are recorded as non-passing results. Only
development cases are exposed through the mutation-input seam.

Evaluation compares baseline and candidate results on the same suite, seed, and
runner configuration. Candidate result bundles are bound to the suite,
configuration digest, candidate provenance, asset reference, and candidate digest.
The candidate asset must be a real candidate directory containing `SKILL.md`,
`change.diff`, and `provenance.json` below a `.axel-candidate-root` marker; its
content and provenance digests are checked before results are evaluated. Hard validator,
regression, held-out, evidence, token, context, and optional cost gates reject
unsafe candidates. Eligible evaluations may update a separate champion registry;
evaluation never writes active or approved skills.
The engine validates result evidence supplied by a runner but does not execute
arbitrary skill text; production callers must use a trusted local runner or add
their own signed execution attestation.

Approval records the operator's decision and exact candidate/evaluation digests,
including the evaluated candidate provenance snapshot. Promotion rechecks the
evaluation semantics from the raw comparison evidence before writing anything.
Promotion writes the approved asset, provenance, and manifest in one local Git
commit. Rollback changes only the approved manifest pointer and records the
restored digest in a later local commit; no command pushes or changes host
permissions.

Retrieval reads only the active approved manifest entries. It ranks skill
sections with a deterministic metadata/text scorer, applies threshold and item/
token budgets, and writes an attribution record containing the exact asset and
section digests. Candidate, rejected, and rolled-back versions are never used;
retrieval failures produce no injected context rather than falling back to
unapproved content.

Compounding is a bounded batch operation. It retains generator, reflector, and
curator artifacts under `data/compound/`, writes JSON/Markdown reports under
`reports/`, and only creates reviewable candidates. It does not modify active
or approved assets; approval, promotion, rollback, and retrieval remain
explicit later steps.

## Development checks

```text
python -m unittest discover -s tests -v
node --test integrations/opencode-plugin/test/*.test.mjs
```

## Optional host capture adapters

Ticket 02 adds adapters under `integrations/`; they are not installed or
enabled by this repository.

- OpenCode: copy or symlink `integrations/opencode-plugin/axel-capture.js` into
  the target project's `.opencode/plugins/` directory. OpenCode documents that
  project plugins are loaded from that directory and that plugins may use the
  generic `event` hook plus `tool.execute.before` / `tool.execute.after` hooks:
  https://opencode.ai/docs/plugins/
- Codex: copy `integrations/codex-hooks` to a local, trusted location, replace
  `<ABSOLUTE_PATH>` in `hooks.json.example` with an absolute path using forward
  slashes (for example `C:/Tools/axel/codex-hooks`), then add the resulting
  hooks file to the target project's `.codex/` layer and review it with
  `/hooks`. The template includes `commandWindows` for Windows hosts. The
  command reads only the documented JSON stdin payload; it does not parse the
  transcript path. See https://learn.chatgpt.com/docs/hooks

Both adapters write only sanitized, bounded envelopes. Set `AXEL_IMPROVE_ROOT`
to choose the local engine directory (default: `.axel-improve` in the host
working directory). They attempt `axel-improve ingest` with a one-second bound;
if it is unavailable, they leave an atomically written JSONL outbox record under
`$AXEL_IMPROVE_ROOT/spool/outbox/` and return success to the host. Ingest a
retained record explicitly with `axel-improve ingest --root <ROOT> --input
<OUTBOX_FILE>`. OpenCode file-edit events do not carry a session ID, so the
plugin associates them with the latest observed session. No provider, skill,
approval, or permission settings are changed.

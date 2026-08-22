---
title: "Human-Gated Assistant Improvement Loop"
date: 2026-08-03
category: docs/solutions/architecture-patterns
module: "Axel Improvement Engine"
problem_type: architecture_pattern
component: assistant
severity: medium
applies_when:
  - "Trajectory evidence may be converted into reusable assistant behavior."
  - "A proposed change could affect future assistant behavior."
tags:
  - assistant-improvement
  - candidate-lifecycle
  - human-gating
  - provenance
  - approved-retrieval
related_components:
  - development_workflow
  - tooling
---

# Human-Gated Assistant Improvement Loop

## Context

Assistant improvement candidate generation is a safety and quality boundary,
not an automatic skill-editing step. The Axel Improvement Engine stores
bounded, redacted trajectory records, performs deterministic-first diagnosis,
and keeps recurring evidence separate from the original trajectories. Its
README says that one-off or unknown-target cases are non-promotable and that
compounding creates only reviewable candidates without modifying active or
approved assets (`README.md:43-46`, `README.md:82-86`).

The evidence threshold is part of the implementation. Diagnosis groups
sanitized trajectories and assigns `one_off` when a group has fewer than the
configured minimum recurrence. After recurrence is sufficient, it assigns
`unresolved` when the target or confidence is insufficient, and `eligible`
only when the recurrence and target checks pass.
Only an eligible diagnosis that is not the one-off class is marked promotable
(`src/axel_improve/diagnose.py:262-294`). The default compound configuration
requires at least two occurrences and target confidence of at least `0.5`
(`src/axel_improve/compound.py:77-97`).

There are two distinct meanings of improvement:

- `/compound` is the runtime engine path. `axel-improve compound` diagnoses a
  bounded batch, generates proposals, reflects and curates them, materializes
  candidate directories, and writes artifacts and reports.
- `/ce-compound` is the documentation workflow used to capture a durable
  solution in the knowledge store. It documents the human-gated pattern; it
  is not the runtime candidate generator.

The resulting rule for the default trajectory-to-diagnosis path is: generate
improvement candidates from repeated, attributable evidence, then keep them
inactive until evaluation, explicit approval, and promotion have all taken
place. Caller-supplied diagnoses remain a trusted input boundary and must be
validated by the caller for correspondence to ledger evidence.

## Guidance

Use this lifecycle as the default boundary:

```text
sanitized trajectories
        -> deterministic diagnosis
        -> recurring, targeted, promotable evidence
        -> generator / reflector / curator
        -> isolated candidate with provenance and diff
        -> evaluation artifact
        -> explicit operator approval
        -> Git-backed promotion into the approved manifest
        -> optional explicit retrieval of approved context
```

1. Start with evidence, not a proposed skill. `diagnose_trajectories` derives
   signals from trajectory metadata, corrections, validators, tool failures,
   routing activity, and outcomes, then groups equivalent or similar signals.
   A group below the recurrence minimum is `one_off`; an unknown or
   low-confidence target is `unresolved`; only the remaining groups become
   eligible (`src/axel_improve/diagnose.py:139-210`,
   `src/axel_improve/diagnose.py:231-294`).

2. Let the compound boundary filter again. `CompoundGenerator.generate`
   accepts only diagnoses with status `eligible`, `promotable` set to true,
   enough distinct trajectory IDs, and sufficient target confidence
   (`src/axel_improve/compound.py:369-385`). This second filter matters when
   diagnoses are supplied by a caller rather than produced in the same run;
   the compound boundary does not independently resolve caller-supplied IDs
   against the ledger. The compound tests show that an injected one-off
   diagnosis does not bypass the compound thresholds
   (`tests/test_compound.py:121-143`).

3. Preserve the reasoning chain. Generated proposals retain diagnosis,
   trajectory, evidence, event, rationale, evaluation-rule, target-confidence,
   and recurrence information (`src/axel_improve/compound.py:100-143`).
   Reflection groups proposals by capability identity and marks multiple
   content variants as a conflict; curation consolidates each group into one
   reviewable proposal while retaining contributing IDs and conflict state
   (`src/axel_improve/compound.py:388-409`,
   `src/axel_improve/compound.py:428-488`).

4. Materialize only below the candidate root. `propose_skill_candidate`
   rejects diagnoses that are not eligible and promotable, excludes the
   one-off diagnosis class, requires recurrence of at least two and target
   confidence of at least `0.5`, and requires evidence and proposed content
   (`src/axel_improve/candidates.py:293-365`). Targeted revisions require a
   resolvable active target; explicit new-skill proposals may omit one. A
   created candidate is a separate directory containing `SKILL.md`,
   `provenance.json`, and
   `change.diff`; its provenance starts with status `proposed`
   (`src/axel_improve/candidates.py:440-484`). The candidate module validates
   the boundary, writes only beneath the configured candidate directory, and
   never executes proposed text (`src/axel_improve/candidates.py:1-5`).

5. Treat evaluation as a gate, not activation. The CLI evaluates a candidate
   against a replay suite and writes an evaluation artifact; a proposed
   candidate may then be recorded as `tested`, but remains outside the
   approved manifest. Promotion-side validation binds the evaluation to the
   exact candidate asset, digests, target, parent digest, and asset key
   (`src/axel_improve/promotion.py:378-427`). Evaluation does not write active
   or approved skills (`README.md:54-65`).

6. Require an explicit approval record. Approval records the operator, reason,
   exact candidate and evaluation digests, and the decision. An approved
   decision moves the candidate to `awaiting_approval`, not to an active skill;
   a rejection moves it to `rejected` (`src/axel_improve/promotion.py:838-934`).

7. Promote only after the exact approval and evaluation are rechecked.
   Promotion requires the approved decision and matching approval digest, then
   writes the approved asset, approved provenance, and manifest in one local
   Git commit (`src/axel_improve/promotion.py:1213-1334`). Rollback is also
   explicit; it records the previous restored digest when one exists and
   removes the active entry when there is no previous version
   (`src/axel_improve/promotion.py:1368-1472`).

8. Treat retrieval as a separate, fail-closed consumer. Retrieval reads only
   current active entries in the approved manifest, applies budgets, and
   records exact asset, evaluation, provenance, and section digests
   (`src/axel_improve/retrieval.py:434-559`). Candidate, rejected, and
   rolled-back versions are excluded. The repository does not automatically
   inject retrieval into OpenCode; host integration remains an explicit
   deployment decision (`README.md:75-86`, `README.md:95-121`).

## Why This Matters

Evidence gating limits overfitting. A single mistake can be ambiguous, caused
by transient context, or specific to one task. Automatically turning it into a
skill would make a temporary observation part of future assistant behavior.
The diagnosis and candidate tests make the intended policy executable:
one-off diagnoses are non-promotable, supplied ineligible diagnoses cannot
bypass the thresholds, and active skills remain unchanged when a candidate is
proposed (`tests/test_diagnose.py:55-75`, `tests/test_compound.py:121-143`,
`tests/test_candidates.py:44-62`).

Provenance and exact digests make review meaningful. A reviewer can inspect
the generated diff, source trajectory and evidence IDs, parent digest for a
targeted revision, and evaluation result for the same candidate bytes.
Tampering with candidate content prevents guarded transitions
(`tests/test_candidates.py:139-149`, `tests/test_promotion.py:259-283`).

The separation limits blast radius. Compounding writes candidate and report
artifacts, not active or approved assets. Evaluation never writes active or
approved skills. Retrieval refuses unapproved content instead of falling back
to a candidate or rejected version (`README.md:61-65`, `README.md:75-86`).

Human gating preserves judgment where code cannot establish intent. The engine
can validate recurrence, target confidence, evaluation semantics, regression
limits, budgets, and digests. The design leaves organizational policy and
operator authorization to the explicit human review step; the current
operator field is recorded and validated as text, not authenticated.

## When to Apply

- Apply this pattern whenever trajectory evidence may become a skill, routing
  rule, validator, memory, playbook, recovery procedure, template, or other
  reusable assistant behavior.
- Keep a one-off mistake in the evidence and diagnosis record while
  investigating it; do not create a skill merely because the mistake is vivid.
- Require repeated evidence from distinct trajectories and a resolvable target
  for targeted revisions before proposing a candidate. Explicit new-skill
  proposals may use an unknown target when the proposal declares that intent.
- Preserve conflicts when several proposals describe the same capability;
  do not silently select a winner.
- Review the exact candidate diff and provenance before evaluation and approval.
- Run replay evaluation before approval and require all hard gates before
  promotion.
- Use retrieval only after promotion places the asset in the approved
  manifest.
- Treat OpenCode host integration as an explicit deployment decision.
- Use `/ce-compound` after a verified learning is worth preserving as
  documentation. It records the lesson; it does not replace candidate
  evaluation or approval.

## Examples

### Recurring Existing-Skill Improvement

Two eligible trajectories for the same target can produce one candidate with
provenance and a readable diff, while active and approved roots remain
unchanged (`tests/test_compound.py:72-90`, `tests/test_compound.py:145-160`).
The intended sequence is:

```text
axel-improve compound --root ./runtime --seed 21
axel-improve evaluate --root ./runtime --suite ./runtime/data/replay/suite-<ID>.json ...
axel-improve approve --root ./runtime --candidate-id <ID> ... --operator <OPERATOR> --reason "reviewed exact diff and evaluation"
axel-improve promote --root ./runtime --candidate-id <ID> ... --approval-digest <APPROVAL_DIGEST> --repo-root .
axel-improve retrieve --root ./runtime --query "review deployment safety" --task-id task-004 --output ./retrieved-context.json
```

The first command creates review material; it does not make new content
retrievable. The final command explicitly reads approved-manifest content. A
host caller would still need to decide how to provide that result to OpenCode.

### One-Off Mistake

A diagnosis with one trajectory is `one_off` and non-promotable. The correct
response is to retain the event as evidence, look for recurrence or
corroboration, and avoid bypassing the boundary with a hand-written candidate
mapping (`src/axel_improve/diagnose.py:268-277`,
`tests/test_compound.py:121-143`).

### Conflicting Proposals

Two eligible proposals for one capability with different content are grouped
under one identity. Reflection marks the conflict and curation creates one
consolidated proposal while preserving the disagreement for evaluation and
approval (`tests/test_compound.py:92-119`).

### Regressive Candidate

A candidate can be well-formed and still be rejected. Evaluation and approval
must pass before promotion; a later regression can be rolled back so retrieval
no longer returns the rolled-back version (`tests/test_compound.py:249-305`,
`tests/test_retrieval.py:296-359`).

## Related

- `README.md:43-86` describes the ledger, non-promotable cases, evaluation
  gates, explicit lifecycle, approved-only retrieval, and compound boundary.
- `src/axel_improve/diagnose.py` defines recurring evidence grouping, diagnosis
  statuses, target confidence, and promotability.
- `src/axel_improve/compound.py` defines bounded generation, reflection,
  curation, candidate materialization, artifacts, and reports.
- `src/axel_improve/candidates.py` defines the isolated candidate format,
  provenance, guarded transitions, digest checks, and candidate-root boundary.
- `src/axel_improve/promotion.py` defines evaluation revalidation, explicit
  approval, Git-backed promotion, manifest history, and rollback.
- `src/axel_improve/retrieval.py` defines approved-only retrieval and
  attribution; `src/axel_improve/cli.py` exposes it as an explicit command.
- `tests/test_compound.py`, `tests/test_candidates.py`,
  `tests/test_promotion.py`, and `tests/test_retrieval.py` provide lifecycle
  evidence for non-mutation, provenance, gates, promotion, rollback, and
  approved-only retrieval.
- `/compound` and `/ce-compound` are complementary: the former governs runtime
  improvement candidates, while the latter captures verified institutional
  knowledge. This document does not propose changing `/ce-compound`.

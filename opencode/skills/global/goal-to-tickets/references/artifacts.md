# Artifact Contracts

## Existing solutions

Write `.planning/<goal-slug>/research/existing-solutions.md`:

```markdown
# Existing Solutions: <Goal>

## Search Scope
Queries, constraints, language/ecosystem, and search date.

## Requirements Used For Fit
- Requirement

## Candidates
### <Repository>
- **URL:**
- **What it solves:**
- **Fit:** Strong / Partial / Weak
- **License:**
- **Maintenance evidence:**
- **Security considerations:**
- **Integration effort:**
- **Gaps and risks:**

## Comparison
A concise evidence-based comparison.

## Reuse Decision
**Decision:** Adopt directly / Adopt with adapters / Fork and modify / Combine existing projects / Build custom

Reasoning and consequences.
```

## Final specification

Write `.planning/<goal-slug>/final-spec.md`:

```markdown
# <Goal Name>

## Destination
The measurable end state.

## Context And Problem
Why the goal matters and the current condition.

## Users And Workflows
Who uses it and the important end-to-end journeys.

## Scope
### In Scope
### Non-Goals

## Constraints And Assumptions
Distinguish confirmed constraints from assumptions.

## Existing-Solution Decision
Summary and link to `research/existing-solutions.md`.

## Recommended Design
Modules, interfaces, state/data, integrations, and important behavior.

## Production Requirements
Reliability, security, performance, observability, deployment, migration, rollback, and support expectations that apply.

## Alternatives Considered
Rejected approaches and why.

## Risks And Mitigations
Concrete, goal-specific risks and checks.

## Acceptance Criteria
- [ ] Observable criterion with stable identifier `AC-01`

## Open Implementation Details
Only decisions safe to leave to individual tickets.

## Ticket Traceability
Completed after ticket creation: map each `AC-*` to ticket numbers.
```

## Implementation ticket

Write one file per ticket as `.planning/<goal-slug>/tickets/<NN>-<slug>.md`:

```markdown
# <NN> - <Outcome-oriented title>

## Outcome
The complete user-visible or operational behavior delivered by this slice.

## Why This Slice Exists
The specification requirements and acceptance criteria it advances.

## What To Build
Implementation boundaries without brittle file-by-file instructions.

## Acceptance Criteria
- [ ] Verifiable behavior

## Verification
Tests, commands, observations, or demonstrations proving the outcome.

## Production Considerations
Security, failure behavior, observability, migration, deployment, or rollback requirements that apply. Use `None` when genuinely irrelevant.

## Blocked By
Ticket numbers and titles, or `None - can start immediately`.

## Traceability
- `AC-01`
```

## Ticket quality checks

- The title names an outcome, not a technical layer.
- Acceptance criteria are observable and unambiguous.
- Verification is possible without relying on the author's memory.
- The slice can fit in one fresh agent context.
- Dependencies are real prerequisites, not preferred ordering.
- Completing all tickets closes every acceptance criterion.

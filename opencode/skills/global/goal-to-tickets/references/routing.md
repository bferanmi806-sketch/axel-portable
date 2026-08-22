# Adaptive Routing

Route by semantic evidence in the goal and repository. Load a supporting skill only when its procedure changes the planning outcome.

| Signal | Route | Exit condition |
|---|---|---|
| Goal, user, outcome, or scope is ambiguous | `grilling` | Decision-changing ambiguity is resolved |
| Domain terms conflict or remain overloaded | `domain-modeling` | Canonical terms and relationships are clear |
| External claims or APIs need evidence | `research`, `source-driven-development` | Primary-source findings answer the question |
| Existing software may cover the goal | GitHub reuse scan | Reuse decision is evidence-backed |
| Several decisions depend on unknown prior decisions | `wayfinder` | The route can be expressed as a plan |
| A behavior or architecture claim is cheaper to test than debate | `prototype` | Prototype answers the named question |
| Multiple interfaces or architectures are credible | `design-an-interface`, `codebase-design` | Alternatives are compared and one is recommended |
| Failure is costly, irreversible, or operationally dangerous | `thinking-pre-mortem` | Top concrete risks have mitigations and checks |
| Trust boundaries or sensitive data exist | `security-and-hardening` | Threats and required controls are reflected in the spec |
| Recommendation relies on a load-bearing assumption | `doubt-driven-development` | Findings are reconciled or uncertainty is surfaced |
| Delivery mechanics affect correctness | `ci-cd-and-automation` | Deployment, migration, rollback, and gates are planned |

## Scale calibration

### Small clear change

Run the reuse scan, verify assumptions, write the specification, and create a small ticket set. Do not manufacture alternatives or prolonged grilling.

### Substantial understood goal

Run focused grilling, reuse research, relevant design comparison, one hardening pass, specification, and tickets.

### Foggy production goal

Run focused grilling, reuse research, wayfinding, targeted research/prototypes, alternative design, relevant hardening, specification, and tickets.

## Escalation rules

- If research contradicts the requested approach, show the evidence and ask whether the constraint is intentional.
- If no candidate repository is credible, continue with custom design rather than forcing reuse.
- If the user cannot decide a consequential trade-off, recommend a reversible default and record the assumption.
- If an unresolved decision could invalidate many tickets, do not write implementation tickets yet.
- If a question affects only local implementation detail, leave it to the implementing ticket rather than extending the interview.

---
name: goal-to-tickets
description: Turn a substantial software goal, vague product idea, or production-system proposal into a researched final specification and a complete dependency-ordered set of local implementation tickets. Use this whenever the user wants to plan software from an initial goal through executable tickets, especially when requirements are uncertain, architecture must be compared, production readiness matters, or an existing GitHub project might avoid custom development. Do not use for implementing an existing ticket, diagnosing a specific bug, or merely reviewing an already-complete plan.
---

# Goal To Tickets

Transform a software goal into two approved local artifacts: a final specification and implementation tickets covering the first enabling slice through the completed goal. Stop before implementation.

This is an adaptive orchestrator. Use only the planning disciplines the goal actually needs. A small clear feature should move quickly; an uncertain production system should receive research, design comparison, and hardening.

## Non-negotiable boundaries

- Write artifacts locally under `.planning/<goal-slug>/`.
- Never create, edit, or publish GitHub issues.
- Never implement the tickets during this workflow.
- Perform a lightweight GitHub reuse scan before proposing custom architecture for every substantial software goal.
- Prefer adopting, adapting, or composing maintained software over inventing a framework without evidence.
- Ask one decision question at a time and include a recommended answer.
- Look up discoverable facts instead of asking the user.
- Preserve human ownership of product, scope, and consequential trade-off decisions.

## Required outputs

```text
.planning/<goal-slug>/
|-- final-spec.md
|-- research/
|   `-- existing-solutions.md
`-- tickets/
    |-- 01-<first-slice>.md
    |-- 02-<next-slice>.md
    `-- ...
```

Read [references/artifacts.md](references/artifacts.md) before writing these files.

## Workflow

### 1. Orient to the goal

Inspect the current conversation, repository instructions, domain documentation, ADRs, existing planning files, and relevant code. Do not make the user repeat available information.

State a provisional destination in one or two sentences: what will be true when the goal is complete. Identify whether this is a small clear change, a substantial understood goal, or a foggy multi-decision effort.

Read [references/routing.md](references/routing.md) and select only the necessary routes.

### 2. Run the reuse scan

Read [references/github-scan.md](references/github-scan.md). Use the GitHub CLI when available. Search before designing so the plan is grounded in what can be reused.

Write `.planning/<goal-slug>/research/existing-solutions.md`. Conclude with exactly one reuse decision:

- Adopt directly
- Adopt with adapters
- Fork and modify
- Combine existing projects
- Build custom

`Build custom` requires evidence explaining why the strongest candidates fail the goal's requirements.

### 3. Reach shared understanding

Apply the installed `grilling` discipline, with `domain-modeling` when domain language matters:

- Build a decision map from unresolved choices that could materially change the plan.
- Ask the highest-impact unblocked decision first.
- Ask one question at a time and wait for the answer.
- Include a recommended answer and its main trade-off.
- Record resolved terminology and consequential decisions as they crystallize.
- Avoid implementation trivia that can safely be decided inside a ticket.

Do not interpret "relentlessly" as endless questioning. Stop grilling when the readiness gate below is satisfied.

### 4. Resolve blocking uncertainty

Use semantic routing rather than running every skill:

- Use `research` and `source-driven-development` for external facts or framework claims.
- Use `wayfinder` when the route cannot yet be stated because several dependent decisions remain hidden in fog.
- Use `prototype` when cheap executable evidence would settle a behavior or architecture question.
- Use `design-an-interface` or the Design It Twice procedure from `codebase-design` when materially different interfaces or architectures are plausible.

Research and prototypes answer decisions. They are not implementation tickets and should not expand into production work.

### 5. Compare viable plans

For meaningful architectural choices, develop up to three genuinely different plans:

1. Lean: fastest credible route to useful value.
2. Resilient: strongest production reliability and safety.
3. Simple: lowest long-term conceptual and operational burden.

Do not generate three cosmetic variations. If only one approach is viable after research, state why and continue.

Compare using requirement coverage, reuse, complexity, delivery effort, reliability, security, testability, operability, maintainability, reversibility, and cost. Use ratings only with written evidence. Synthesize one recommendation rather than selecting by arithmetic alone.

### 6. Harden the recommendation

Apply only relevant lenses:

- `thinking-pre-mortem` when failure would be expensive or difficult to reverse.
- `security-and-hardening` when untrusted input, identity, secrets, sensitive data, or external services are involved.
- `doubt-driven-development` when a consequential recommendation rests on uncertain assumptions.
- `ci-cd-and-automation` when production delivery, migration, rollback, or operational gates materially affect the design.

Fold valid findings into the recommendation. Do not create review theatre by repeatedly scoring an unchanged plan.

### 7. Pass the readiness gate

Proceed when all of these are true:

- The destination and success criteria are measurable.
- Users, important workflows, constraints, and non-goals are understood.
- The GitHub reuse scan supports the reuse decision.
- Material architecture, data, integration, security, and operational choices are resolved.
- No open question could substantially invalidate the recommended plan.
- Remaining uncertainty is explicit and safe to resolve inside a ticket.
- The user confirms that shared understanding is sufficient.

Perfect knowledge is not required. Decision-changing uncertainty must be resolved.

### 8. Write the final specification

Apply the synthesis discipline from upstream `to-spec`, but override its tracker behavior: write only `.planning/<goal-slug>/final-spec.md` using [references/artifacts.md](references/artifacts.md).

The specification is the canonical statement of the goal. Include the reuse decision, recommended design, rejected alternatives, production concerns, acceptance criteria, and traceable requirements.

### 9. Produce implementation tickets

Apply the tracer-bullet and dependency rules from upstream `to-tickets`, but override its publishing behavior: write one local file per ticket under `.planning/<goal-slug>/tickets/`.

Tickets must:

- Cover the complete journey from the first enabling change to the end goal.
- Deliver narrow, complete vertical behavior rather than horizontal layer work.
- Be independently verifiable and sized for one fresh agent context.
- Declare genuine blocking edges.
- Include testing and production verification in the slice that needs them.
- Include migration, observability, deployment, and rollback slices when the specification requires them.
- Avoid brittle file paths and speculative code snippets.

Number tickets in dependency order. Parallel tickets may share the same blockers but still receive distinct numbers.

### 10. Verify completeness and stop

Before reporting completion:

- Map every specification requirement and acceptance criterion to one or more tickets.
- Check that every ticket traces back to the specification.
- Check for orphan tickets, dependency cycles, hidden horizontal phases, and missing operational work.
- Check that completing all tickets would actually satisfy the destination.
- Present the artifact paths, reuse decision, recommended plan, ticket count, and implementation frontier.

Stop. Do not start ticket 01.

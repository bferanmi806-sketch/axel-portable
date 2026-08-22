---
name: chatgpt-adviser
description: Use when a decision, technical problem, comparison, or workflow needs external research, an independent second opinion, current web evidence, or multiple perspectives. Generates a precise copyable brief for the user's external research system, then evaluates returned evidence against the local codebase and constraints.
---

# ChatGPT Adviser

Use this skill automatically when local inspection cannot establish a high-confidence answer and external research would materially improve the decision. This is a handoff skill: create the research brief for the user to run in their external system; do not send information externally yourself.

## Trigger Criteria

Activate when one or more of these apply:

- Comparing repositories, libraries, vendors, frameworks, platforms, or implementation approaches where reliability, security, licensing, maintenance, compatibility, or cost matters.
- Making an architecture, data, privacy, security, operational, or product decision that is expensive to reverse.
- Investigating a difficult bug, performance regression, or integration failure that remains uncertain after proportionate local inspection.
- Designing a complex workflow that needs independent perspectives to validate assumptions, expose failure modes, or test a recommendation.
- Needing authoritative or current information beyond the local codebase: official documentation, source code, release and issue history, real-world operating experience, or ecosystem status.
- The user asks for research, a comparison, an outside view, a second opinion, or an adversarial review.

Do not activate for ordinary implementation, contained debugging, small reversible decisions, or facts verifiable from local evidence.

## First Inspect Locally

Before generating a brief:

1. Inspect relevant code, configuration, documentation, logs, test results, and prior decisions.
2. State the specific decision or uncertainty. Do not hand off a vague topic.
3. Separate confirmed local facts from assumptions and unknowns.
4. Identify the evidence that could change the recommendation.
5. Sanitize secrets, private data, credentials, and unnecessary personal information. Tell the user what sensitive context was intentionally excluded when material.

## Generate the Handoff Brief

Give the user one self-contained, copyable prompt. Adapt the sections below to the task. Do not include irrelevant boilerplate.

```text
Research Brief: [short decision title]

Decision to make
[One precise decision and the intended outcome.]

Known context
- Confirmed facts: [only verified local facts]
- Current constraints: [platform, budget, architecture, timeline, privacy, compatibility, and non-negotiables]
- Existing decision/history: [only if relevant]
- Assumptions and unknowns: [explicit list]

Research tasks
1. [Question whose answer would decide or narrow the choice.]
2. [Question about implementation, compatibility, operating requirements, or evidence.]
3. Challenge the leading option: explain the strongest reasons it could fail or be the wrong choice.
4. Identify material unknowns and the minimum smoke tests required before adoption.

Evidence standard
- Prefer official documentation, source code, primary records, direct tests, release history, issue trackers, and security advisories.
- Do not treat README claims, marketing, or social discussion as established fact without corroboration.
- Link every material claim to evidence.
- Clearly label each conclusion as confirmed fact, source claim, inference, or unknown.

Required response
1. A concise executive recommendation.
2. A comparison table covering [tailored decision criteria].
3. Evidence links next to each material claim.
4. The strongest counterarguments and failure modes.
5. Rejected options and why they were rejected.
6. A reversible rollout plan, including validation and rollback steps.
7. Any questions or tests that must be resolved before deciding.

Optimize for [the user's actual priorities], not feature count or popularity.
```

## Multiple-Perspective Mode

For complex workflows or high-impact decisions, explicitly require these independent lenses. Omit only lenses that cannot materially affect the outcome.

- Architecture: design fit, integration boundaries, scalability, and technical constraints.
- Security and privacy: data flow, permissions, supply chain, secrets, retention, and attack surface.
- Operations: installation, dependencies, observability, upgrades, backup, recovery, and ongoing maintenance.
- Compatibility: operating system, runtime, API version, deployment environment, and migration risk.
- Workflow fit: user experience, team practices, automation, and realistic failure modes.
- Adversarial review: strongest case against the tentative recommendation, alternatives, and what evidence would reverse it.

Ask the external system to present disagreements between lenses explicitly. Do not accept a blended answer that conceals trade-offs.

## When Findings Return

1. Inspect the returned sources and distinguish evidence from unsupported conclusions.
2. Reconcile the research with local code, direct tests, project constraints, and the user's priorities.
3. Identify conflicts, gaps, stale information, and claims that need local verification.
4. Give a ranked recommendation, with a clear decision rather than an unprioritized option list.
5. Implement only the smallest safe next step after required smoke tests pass.
6. Do not reopen a settled decision unless new evidence materially changes it.

## Control

- The user can say `skip external research` to proceed locally.
- Never transmit or request secrets, credentials, private repositories, or sensitive personal data in the handoff.
- When research is not needed, continue work directly rather than producing a prompt.

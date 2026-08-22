---
title: ChatGPT Adviser Skill
type: guide
permalink: axel/system/chat-gpt-adviser-skill
tags:
- opencode
- research
- decision-support
---

## Purpose
`chatgpt-adviser` is a global OpenCode skill for decisions that need external research, an independent second opinion, current web evidence, or multiple perspectives.

## Workflow
1. Inspect local code, evidence, and constraints first.
2. Generate one precise, copyable brief for Enoch's external research system.
3. Require source-linked evidence, clear distinctions between fact and inference, counterarguments, risks, and reversible validation steps.
4. Evaluate returned findings against the local codebase and Enoch's actual priorities before recommending or implementing a decision.

## Boundaries
- It does not transmit information externally.
- It sanitizes sensitive context from research briefs.
- It is unnecessary for small, reversible, locally verifiable work.
# Workflows

This is a derived map of the current workflow routing. The canonical rules are
in `AGENTS.md`; individual skills remain authoritative for their own contracts.

## Memory

1. Start with a compact overview of stable profile, preferences, priorities and active projects.
2. Retrieve only additional memories directly relevant to the request.
3. Use CodeMem for session history and Basic Memory for curated durable context.
4. Before a meaningful Basic Memory write, state what will be saved and wait for approval unless an explicitly approved workflow says otherwise.

## Engineering

1. Inspect the current system and evidence.
2. Understand the real problem and choose the smallest useful change.
3. Use the relevant installed skill or tool rather than recreating it.
4. Implement, verify with real evidence, and report exact limitations.

## Research and routing

- Use Wigolo for ordinary web research and URL retrieval.
- Use Agent Reach for supported platform-specific retrieval and GitHub work through `gh`.
- Use Context7 for current library, framework, SDK and API documentation.
- Use `chatgpt-adviser` when an independent, source-linked perspective would materially improve a consequential decision.

## Improvement and curation

- `ce-compound` produces reviewable improvement candidates; it does not silently approve or promote them.
- Dream Mode mines CodeMem into proposals and keeps Basic Memory changes approval-gated.
- The Axel Improvement Engine stores sanitized local trajectories and does not modify active skills automatically.
- The experimental OpenCode local-memory adapter remains disabled by default until its loader and capture behavior are proven in a disposable profile.

## Failure handling

- Stop after three consecutive failures with the same root error.
- Preserve the exact error and switch to a materially different safe approach or ask the user.
- Do not confuse unchanged state during an explicitly requested monitor/wait with a failure.

# Codex Runtime

Codex is a reusable alternate runtime and heavier verification environment.
Use `config.toml.template` as a reviewed baseline, not as a drop-in copy of
the current machine config. It intentionally omits authentication, browser
state, project trust history, native-pipe paths, microphone identifiers and
machine-specific notification commands.

The general Codex skills overlap with `opencode/skills/global/`; the Codex-only
memory skills and paused automations are preserved here. Codex generated memory
and session history are not canonical Axel memory and are excluded.

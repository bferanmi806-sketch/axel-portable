---
title: 'OpenPets #133 Sticky Realtime Mute During Startup'
type: bugfix
permalink: axel-project-knowledge/contributions/openpets/bugfix/open-pets-133-sticky-realtime-mute-during-startup
repository: alvinunreal/openpets
upstream: https://github.com/alvinunreal/openpets
issue: 10
pull_request: 133
branch: main
contribution_type: bugfix
category: bugfix
---

## Problem

PR #133 added a host-private realtime voice conversation to `alvinunreal/openpets`. During asynchronous renderer startup, callers could mute or unmute before the hidden Electron renderer had loaded, assigned its session ID, and acquired the microphone.

## Symptoms

`VoiceConversationService` updated its snapshot to `muted: true`, but `ElectronVoiceRealtimeTransport` sent the mute IPC command immediately. The renderer discarded that command because its session ID was not established yet. The eventual microphone track therefore remained enabled while host state reported muted.

## What Didn't Work

Treating mute as a one-time IPC command at any point after transport construction was insufficient. Renderer load completion and microphone/session readiness are separate asynchronous boundaries, and a command sent before the renderer accepted its session identity could not be replayed.

## Root Cause

Mute intent was modeled as an edge-triggered command rather than sticky desired state. The transport had no readiness-aware replay path after the renderer established its session and microphone track.

## Solution

`ElectronVoiceRealtimeTransport` now retains `desiredMuted` for the lifetime of the transport and tracks microphone readiness. `setMuted()` always updates the desired value, sends immediately only when the renderer is ready, and reapplies the latest value when the `microphone-acquired` event establishes a valid session and track. Startup updates are last-write-wins; ready-session updates retain their immediate behavior.

The regression fake models a real microphone track and covers `start -> mute before connected -> acquire/connect`, asserting both the track is disabled and the service snapshot remains muted.

## Why This Works

The desired control state crosses the asynchronous renderer/session boundary as durable intent instead of a lossy command. Applying it at microphone acquisition guarantees that the track exists and the renderer session ID filter accepts the command, while preserving immediate mute/unmute behavior after readiness.

## Tests and Verification

- Focused realtime conversation lifecycle test passed, including the pre-connected mute regression.
- Existing #118 one-shot voice lifecycle test passed.
- Existing gateway, voice bridge, and tray voice tests passed.
- `pnpm typecheck` passed.
- `pnpm build` passed.
- `git diff --check` passed.
- Fix commit: `7caae11a4e66d8b7097e3af451df2bf8b1b00d13`.
- Draft PR: https://github.com/alvinunreal/openpets/pull/133, open against `main` as of 2026-08-14.
- Full-suite limitations were unrelated to this fix: Windows denied the symlink operation in `claude-memory.test.js`, and the packaging contract hit an existing CRLF-sensitive assertion against unchanged `main.ts`.

## Prevention

For IPC, WebRTC, and renderer controls that can be issued during startup, represent desired state explicitly and apply it at the owning readiness boundary. Regression tests should call the control before readiness and assert the underlying resource state, not only the host snapshot.

## Related Issues or PRs

- Issue: https://github.com/alvinunreal/openpets/issues/10
- Pull request: https://github.com/alvinunreal/openpets/pull/133
- Preserved one-shot voice foundation: https://github.com/alvinunreal/openpets/pull/118
- Base already included: https://github.com/alvinunreal/openpets/pull/127
- Source: `apps/desktop/src/voice-realtime-electron.ts`
- Test: `apps/desktop/tests/voice-conversation.test.ts`

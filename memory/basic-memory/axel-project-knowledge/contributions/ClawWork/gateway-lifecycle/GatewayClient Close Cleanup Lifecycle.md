---
title: GatewayClient Close Cleanup Lifecycle
type: contribution
permalink: axel-project-knowledge/contributions/claw-work/gateway-lifecycle/gateway-client-close-cleanup-lifecycle
repository: ClawWork
upstream: https://github.com/clawwork-ai/ClawWork
issue: 230
pull_request: 560
branch: test/230-gateway-client-lifecycle
contribution_type: bugfix
category: gateway-lifecycle
---

# GatewayClient Close Cleanup Lifecycle

## Problem

ClawWork issue #230 requested focused unit coverage for the desktop `GatewayClient` reconnect, timeout, and pending-request lifecycle.

## Symptoms

The desktop WebSocket close handler reset connection state and scheduled reconnect, but pending requests were not rejected immediately. They could remain pending until the reconnect timer called `connect()` and `cleanup()`, or until the 15-second request timeout fired.

## What Didn't Work

The initial focused close-regression test used a lightweight fake WebSocket and confirmed that two requests remained unresolved immediately after a simulated close. A broad desktop test run also reported unrelated Windows path-separator failures in six existing filesystem test files; the same 17 failures reproduced on untouched `upstream/main`.

## Root Cause

`GatewayClient` already owned pending-request rejection and timeout clearing in its private `cleanup()` method, but `ws.on('close')` called only `stopHeartbeat()` before scheduling reconnect. Cleanup was deferred to a later lifecycle transition.

## Solution

Extracted the existing reconnect expression into pure `calculateReconnectDelay(attempt)`, preserving the 3-second base delay, 1-based exponential sequence, and 96-second cap. The close handler now invokes the existing `cleanup()` path immediately, avoiding duplicated pending-request logic and preserving reconnect scheduling, policy-violation handling, heartbeat shutdown, and destruction behavior.

## Why This Works

`cleanup()` clears each pending request timer, rejects each promise with `GATEWAY_CONNECTION_CLOSED`, empties the pending map, detaches the old WebSocket, and leaves only the newly scheduled reconnect timer. Reusing that owner makes close behavior deterministic and prevents delayed timeout rejections.

## Tests and Verification

- Added `packages/desktop/test/gateway-client.test.ts` with capped-backoff, 15-second fake-timer timeout, and multi-request close cleanup tests.
- Focused GatewayClient and TLS tests passed: 8 tests.
- Desktop source and test TypeScript checks passed after building referenced shared/core projects.
- Changed-file ESLint and Prettier checks passed.
- Full desktop suite: 367 passed; 17 unrelated baseline failures reproduced on `upstream/main`.
- `pnpm check` passed lint, architecture, CI-bot security, UI contracts, renderer-copy, i18n, and dead-code checks, then stopped at the repository baseline of 539 pre-existing formatting failures.
- Signed commit `ebea1f98d740808ad46514429a25d394374f1dd2` was pushed to the branch and draft PR #560 was created against `main`. At record time, quality/test/smoke/secrets/bot checks passed while build and gateway checks remained pending.

## Prevention

For transport lifecycle code, write tests at the event boundary and assert both promise settlement and timer/map state. When a class already owns resource cleanup, call that owner from every terminal lifecycle path rather than duplicating rejection and timer logic. Test the first, growing, capped, and beyond-cap backoff attempts explicitly.

## Related Issues or PRs

- Issue: https://github.com/clawwork-ai/ClawWork/issues/230
- Pull request: https://github.com/clawwork-ai/ClawWork/pull/560
- Source: `packages/desktop/src/main/ws/gateway-client.ts`
- Tests: `packages/desktop/test/gateway-client.test.ts`
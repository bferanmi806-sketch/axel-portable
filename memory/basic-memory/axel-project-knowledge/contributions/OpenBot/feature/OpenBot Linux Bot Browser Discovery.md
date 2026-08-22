---
title: OpenBot Linux Bot Browser Discovery
type: contribution
permalink: axel-project-knowledge/contributions/open-bot/feature/open-bot-linux-bot-browser-discovery
repository: OpenBot
upstream: https://github.com/ashhart/OpenBot
issue: null
pull_request: 1
branch: feat/linux-bot-browser
contribution_type: feature
category: browser-support
---

# OpenBot Linux Bot Browser Discovery

## Problem

OpenBOT's persistent per-bot Bot browser was effectively macOS-only even though its browser session, CDP, profile, and takeover layers were platform-neutral. Browser discovery only checked four absolute macOS application-bundle paths. The target probe also reported a browser as ready without checking whether a Chrome-family executable existed.

## Symptoms

On Linux, normal installed browsers exposed as `google-chrome`, `google-chrome-stable`, `chromium`, `chromium-browser`, `microsoft-edge`, `microsoft-edge-stable`, or `brave-browser` could not be found, so the Bot browser could not start. A probe could report readiness before the first-use launch failed.

## What Did Not Work

The implementation did not add distro-specific absolute paths, a new browser abstraction, Playwright/Puppeteer, Windows support, or a Linux packaging target. A first disposable Linux container attempt stalled at Debian package-index setup, and a prebuilt Chromium image initially failed because its sandbox policy disabled unprivileged user namespaces; the final runtime smoke used a validation-only `--no-sandbox` wrapper and did not alter OpenBOT launch flags.

## Root Cause

Discovery was hard-coded to macOS bundle locations instead of using the repository's existing hydrated login-shell PATH resolver. Separately, browser probing bypassed the provider's existing readiness check. Profile roots also needed to converge with `OPENBOT_DATA_DIR` and Linux's `XDG_CONFIG_HOME` convention so probe, normal tools, takeover, and deletion would address the same persistent state.

## Solution

The Linux branch of `findChrome()` resolves the common executable names through the existing `which()` helper and returns only resolved executable paths. The macOS candidate list and order remain unchanged, while unsupported platforms receive an explicit unsupported message. `probeTarget()` now delegates browser checks to the cached `BrowserComputerProvider`, which performs the existing browser and live-profile-lock checks without starting Chrome. Provider creation honors `OPENBOT_DATA_DIR`, and the fallback Linux profile path honors `XDG_CONFIG_HOME`.

## Why This Works

Platform-specific code stops at executable discovery. The established ChromeSession lifecycle still owns `--remote-debugging-port=0`, `DevToolsActivePort`, CDP attach, one persistent profile per bot, and lock-safe cleanup. Reusing the existing provider keeps probe and takeover state aligned, while canonical data-root handling prevents a custom or non-default Linux config location from splitting browser state across directories.

## Tests and Verification

- Upstream `main` was fetched and fast-forwarded before editing; no overlapping open issues or pull requests were present at that time.
- `npm install` completed with no reported vulnerabilities.
- `npm run typecheck` passed.
- `npm run build` passed; the only output warning was the pre-existing Vite dynamic/static VM import warning.
- Disposable Linux Chromium smoke passed discovery, distinct profile directories for two bot IDs, stale `DevToolsActivePort` cleanup, stale `SingletonLock` cleanup, live-lock preservation, CDP launch/attach, screenshot, local HTTP navigation, and stop.
- The final worktree was clean and the five-file change was committed as `09e58a8`, pushed on `feat/linux-bot-browser`, and opened as PR #1 at https://github.com/ashhart/OpenBot/pull/1. The PR was verified open against `main`; tracker state was non-draft as of 2026-08-16.

## Prevention

For cross-platform desktop features, isolate platform discovery at the narrowest boundary and keep the resource lifecycle shared. Reuse existing PATH resolution rather than hard-coding distro paths, and verify that every entry point uses the same durable root before adding a readiness probe. Validate profile lock behavior against the real target runtime, including both stale and live locks, instead of treating cleanup as unconditional.

## Related Issues or PRs

- Pull request: https://github.com/ashhart/OpenBot/pull/1
- Upstream repository: https://github.com/ashhart/OpenBot
- Source: `src/main/tools/computer/browser/chrome.ts`
- Source: `src/main/tools/computer/browser/chromeSession.ts`
- Source: `src/main/tools/computer/browser/profile.ts`
- Source: `src/main/tools/computer/browser/provider.ts`
- Source: `src/main/tools/computer/index.ts`
- Source: `src/main/ipc/botComputerTarget.ts`
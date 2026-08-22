---
title: Paseo Desktop Packaging Must Force the Electron Export
type: contribution
permalink: axel-project-knowledge/contributions/paseo/bugfix/paseo-desktop-packaging-must-force-the-electron-export
repository: paseo
upstream: https://github.com/bferanmi806-sketch/paseo
issue: null
pull_request: null
branch: experiment/opencode-cdesktop-runtime
contribution_type: bugfix
category: packaging
---

# Paseo Desktop Packaging Must Force the Electron Export

## Problem

Paseo desktop packaging could invoke the desktop build without guaranteeing that the Expo web export used the Electron platform configuration. That made the shipped desktop bundle vulnerable to receiving the ordinary web target instead of the Electron-specific runtime.

## Symptoms

The desktop package build depended on an app-export step owned by the root script, while the package-local `build` script did not explicitly run that step. A direct `npm run build --workspace=@getpaseo/desktop` therefore did not reliably establish the required Electron export before `electron-builder` packaged the application.

## What Didn't Work

The previous arrangement embedded the app export inside the root `build:desktop` script. That worked only when callers used that exact root command; it left the package-local build boundary incomplete and made alternate packaging entry points unsafe.

## Root Cause

The Electron-specific environment setting, `PASEO_WEB_PLATFORM=electron`, was attached to one top-level orchestration command rather than enforced by the desktop package's own build path. The packaging boundary did not own all of its required inputs.

## Solution

Split the root app export into `build:desktop:app` and make `packages/desktop` run it before server compilation, main-process compilation, and `electron-builder`:

- `package.json` defines `build:desktop:app` and retains `build:desktop` as the desktop package orchestration entry point.
- `packages/desktop/package.json` invokes `npm --prefix ../.. run build:desktop:app` as the first step of its `build` script.

The export continues to run `cross-env PASEO_WEB_PLATFORM=electron npx expo export --platform web`.

## Why This Works

The desktop package now establishes the Electron renderer bundle at its own build boundary, regardless of whether packaging is initiated from the root workspace script or directly through the desktop workspace. Subsequent server, main-process, and installer steps consume the intended target.

## Tests and Verification

- Current branch: `experiment/opencode-cdesktop-runtime`.
- Worktree is clean at commit `cb9ae1cea971f135c2956302bbcadfe77d1b2e1f`, tagged `axel-paseo-v2`.
- Source review confirms the Electron export guard in `package.json:88` and the package-local invocation in `packages/desktop/package.json:18`.
- Released installer exists at `packages/desktop/release/Paseo-Setup-0.3.1-x64.exe`.
- Installer SHA-256: `D316822E5A5E6329F14145F7E8BDA825CF33826441B7E4BCC08C053B5CDD33FF`.
- Prior release evidence recorded that the installer was attached to the GitHub release and that the OpenCode smoke evidence was captured in `.dev/packaging/v2-opencode-turn.log`.
- The installer is unsigned because Windows `winCodeSign` extraction requires symlink privileges in this environment; signing remains a release-environment concern, not part of this packaging logic fix.

## Prevention

Build scripts that produce a specialized artifact should enforce target-specific configuration at the narrowest package boundary that owns the artifact. Keep a package-local build path complete, and inspect the generated bundle or installer before release rather than validating only the command exit code.

## Related Issues or PRs

- Upstream repository: https://github.com/bferanmi806-sketch/paseo
- Release tag: `axel-paseo-v2`
- Source files: `package.json`, `packages/desktop/package.json`
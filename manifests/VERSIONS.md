# Versions and Provenance

Initial observations were made on Windows/WSL Ubuntu on 2026-08-22. The
current-profile sync audit was completed on 2026-08-25. A version marked `not on
PATH` was identified through source metadata or configuration rather than a
live CLI invocation.

## Runtime and tools

| Component | Observed version/source | Evidence |
|---|---|---|
| OpenCode Windows CLI | `1.18.18` | `opencode --version` |
| OpenCode WSL Ubuntu | Not installed/on PATH | `wsl.exe -d Ubuntu -e opencode --version` failed with command not found |
| Paseo | `0.3.1` | `Documents\paseo-opencode-runtime\package.json` |
| Paseo source checkout | branch `experiment/opencode-cdesktop-runtime`, commit `cb9ae1cea971f135c2956302bbcadfe77d1b2e1f` | Git metadata |
| Basic Memory | `0.23.0` | `basic-memory --version` |
| CodeMem local source | `@codemem/core`, `@codemem/mcp`, `@codemem/opencode-plugin`, `codemem` `0.40.0-alpha.1` | package manifests |
| CodeMem source base commit | `44724456ea4ef91771bd15b1811932988c687eaa` (`v0.40.0-alpha.1-10-g44724456`), dirty working tree | Git metadata |
| Codex CLI | `codex-cli 0.145.0` | `codex --version` |
| Codex reported latest-version file | `0.146.0` | `.codex/version.json`; not the installed CLI version |
| Node.js Windows | `v24.14.0` | `node --version` |
| Node.js WSL Ubuntu | Not installed/on PATH | WSL command check |
| Python Windows | `3.13.14` | `python --version` |
| Python WSL Ubuntu | `3.14.4` | `wsl.exe -d Ubuntu -e python3 --version` |
| uv Windows | `0.11.19` | `uv --version` |
| npm Windows | `11.9.0` | `npm --version` |
| pnpm | Required by vendored CodeMem; local package manager metadata pins `pnpm@11.8.0` | CodeMem `package.json` |
| Git Windows | `2.46.0.windows.1` | `git --version` |
| Git WSL Ubuntu | `2.53.0` | WSL command check |
| Agent Reach | `1.5.0` | `agent-reach --version` |
| gh CLI | `2.96.0` | `gh --version` |
| Unpeel | Not discovered in the Windows profile | Recursive file/reference search under `C:\Users\bfera` |
| Mcporter | `0.12.3` | `mcporter --version` |
| Wigolo | Not installed as a direct Windows command; OpenCode/Codex use `npx` | command check and config |
| Bun | Not installed on Windows PATH | command check |
| yt-dlp | `2026.07.04` | last30days doctor cache |
| Axel Improvement Engine | `0.1.0` | `pyproject.toml` |
| OpenCode local-memory experiment | `0.1.0`, SDK `@opencode-ai/plugin 1.18.5` | package manifest |

## Important pinned/runtime references

- OpenCode global plugin dependency: `@opencode-ai/plugin 1.18.5` in the current Windows config.
- Codemem OpenCode plugin package declares `@opencode-ai/plugin 1.18.9`; the vendored source is the current dirty worktree and should be rebuilt before use.
- The disabled local-memory experiment targets OpenCode/plugin SDK `1.18.5` and Node `24+`.
- Paseo source repository: `https://github.com/getpaseo/paseo.git`.
- CodeMem source repository: `https://github.com/kunickiaj/codemem`.
- Current OpenCode design integration provenance is recorded in `DESIGN-TOOLS.md`; the values below were copied from that live manifest and not independently resolved from the network during this sync.

## Current OpenCode design integrations

| Component | Observed source/version | Treatment |
|---|---|---|
| Interface Design | `Dammyjay93/interface-design`, commit `2f9be3206855bcb2d1d0af262c8bae25cba6658d` | OpenCode local skill |
| Impeccable | `pbakaus/impeccable`, `skill-v4.1.1`, commit `5a149f3fdb1b5793f10567233b1dcab98fc305fd` | OpenCode local skill; separate Codex `4.0.3` copy retained |
| OpenAI Product Design | `openai/role-specific-plugins`, plugin `0.1.50`, commit `fe5608d2512a7d6a7b9821ce8a88c48464ecd6e4` | OpenCode local skill bundle |
| UI/UX Pro Max | `nextlevelbuilder/ui-ux-pro-max-skill`, `v2.15.0`, commit `a38d04c3d5c298c851dbe5e6ee1965ee3de42cb5` | OpenCode local skill |
- OpenCode local-memory reviewed source reference: `pointfish6660/opencode-memory-plugin` commit `c0064bf3d83023ef4729d41aaa97eb8cf9ddf39a`.

The copied UI/UX Pro Max source currently has an upstream validation gap: its
`validate_data.py` reports four stale catalog snapshots, and its bundled tests
refer to refresh/evaluation scripts that are not present in the installed skill
directory. The portable copy preserves that live source unchanged.

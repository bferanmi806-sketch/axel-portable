# Versions and Provenance

Observed on Windows/WSL Ubuntu on 2026-08-22. A version marked `not on PATH`
was identified through source metadata or configuration rather than a live CLI
invocation.

## Runtime and tools

| Component | Observed version/source | Evidence |
|---|---|---|
| OpenCode Windows CLI | `1.18.18` | `opencode --version` |
| OpenCode WSL Ubuntu | Not installed/on PATH | `wsl.exe -d Ubuntu -e opencode --version` failed with command not found |
| Paseo | `0.3.1` | `Documents\paseo-opencode-runtime\package.json` |
| Paseo source checkout | branch `experiment/opencode-cdesktop-runtime`, commit `cb9ae1cea971f135c2956302bbcadfe77d1b2e1f` | Git metadata |
| Basic Memory | `0.22.1` | `basic-memory --version` |
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
- OpenCode local-memory reviewed source reference: `pointfish6660/opencode-memory-plugin` commit `c0064bf3d83023ef4729d41aaa97eb8cf9ddf39a`.

# Portable File Manifest

Inventory date: 2026-08-22. Paths are Windows paths unless explicitly marked
as WSL. The package was assembled by copying source files, not moving or
deleting the working installation.

## Included source

| Source path | Component | Contents | Treatment |
|---|---|---|---|
| `C:\Users\bfera\.config\opencode\AGENTS.md` | Axel/OpenCode | Operating rules and identity behavior | Copied to repository root `AGENTS.md` |
| `C:\Users\bfera\.paseo\config.json` `daemon.appendSystemPrompt` | Axel/Paseo | Global Axel prompt | Copied as `identity/AXEL.md`; other config values sanitized into a template |
| `C:\Users\bfera\.config\opencode\skills` | OpenCode | 14 local frontend/design skills | Copied to `opencode/skills/local/` |
| `C:\Users\bfera\.agents\skills` | Global skills | Installed skills, references, scripts and provenance | Copied to `opencode/skills/global/`, excluding nested Git metadata and generated caches |
| `C:\Users\bfera\.agents\.skill-lock.json` | Global skills | Git source and install provenance | Copied as `opencode/skills/skill-lock.json` |
| `C:\Users\bfera\.config\opencode\commands\compound.md` | OpenCode | Axel Improvement Engine command | Copied to `opencode/commands/compound.md` |
| `C:\Users\bfera\.config\opencode\plans\2026-07-31-001-dream-mode-resilient-curation-plan.md` | OpenCode/Dream Mode | Current planning contract | Copied to `opencode/plans/` |
| `C:\Users\bfera\.config\opencode\package.json` | OpenCode | Plugin SDK dependency | Copied to `opencode/config/` |
| `C:\Users\bfera\.config\opencode\package-lock.json` | OpenCode | Locked plugin dependency graph | Copied to `opencode/config/` |
| `C:\Users\bfera\Axel-memory\Axel` | Basic Memory | Configured `Axel` human-readable notes | Copied to `memory/basic-memory/axel/` |
| `C:\Users\bfera\basic-memory` | Basic Memory | Configured `main` human-readable notes | Copied to `memory/basic-memory/main/` |
| `C:\Users\bfera\Documents\paseo-codex-test\Axel Project Knowledge` | Basic Memory | Configured project contributions and agent lessons | Copied to `memory/basic-memory/axel-project-knowledge/` |
| `C:\Users\bfera\Documents\paseo-codex-test\Axel` | Alternate memory worktree | Separate working copy with newer/uncommitted notes | Copied to `memory/axel-worktree/`; review before merging |
| `C:\Users\bfera\Documents\Axel Workspaces\axel_project` | Axel project knowledge | Active index, architecture, decisions and feature notes | Copied to `memory/axel-project/` |
| `C:\Users\bfera\Documents\Engineering\codemem` | CodeMem | Current local CodeMem source, MCP server, plugin and local changes | Snapshot copied to `tools/integrations/codemem/` without `.git`, dependencies, generated dist/viewer output or databases; viewer source assets under `packages/ui/static` are retained |
| `C:\Users\bfera\Documents\Engineering\axel-improvement-engine` | Axel tooling | Local trajectory, redaction, candidate and compounding engine | Source copied to `tools/integrations/axel-improvement-engine/` without `.git`, caches, bytecode or generated data |
| `C:\Users\bfera\Documents\paseo-codex-test\opencode-local-memory` | Experimental memory | Disabled OpenCode local-memory adapter and tests | Snapshot copied to `tools/integrations/opencode-local-memory/` without dependencies, dist or graph artifacts |
| `C:\Users\bfera\.mcporter\mcporter.json` | MCP | Public Exa MCP endpoint | Copied to `tools/mcp/mcporter.json` |
| `C:\Users\bfera\Documents\paseo-opencode-runtime\paseo.json` | Paseo development | Portable dev-worktree service definitions | Copied to `runtimes/paseo/paseo.json` |
| `C:\Users\bfera\.codex\config.toml` | Codex | Model, plugin, MCP and feature settings | Sanitized reusable template in `runtimes/codex/` |
| `C:\Users\bfera\.codex\rules\default.rules` | Codex | Local command approval rules | Preserved as a workstation-specific snapshot; not auto-applied |
| `C:\Users\bfera\.codex\skills` | Codex | Installed non-system skills and Codex-specific skill files | Copied to `runtimes/codex/skills/` |
| `C:\Users\bfera\.codex\automations` | Codex | Paused Dreamer and workspace automation definitions | Copied to `runtimes/codex/automations/` and marked project-dependent |
| `C:\Users\bfera\.config\yt-dlp\config` | Tool | Node JavaScript runtime setting | Copied as a non-secret tool reference |
| `C:\Users\bfera\.agent-reach\tools\xiaoyuzhou\transcribe.sh` | Tool | Local transcription helper | Copied under tool integrations if present |

## Sanitized or templated

| Source path | Reason | Package treatment |
|---|---|---|
| `C:\Users\bfera\.config\opencode\opencode.json` | Contains absolute local paths and an environment-backed credential reference | `opencode/config/opencode.json.template` |
| `C:\Users\bfera\.paseo\config.json` | Contains absolute paths and runtime-specific provider commands | `runtimes/paseo/config.template.json`; prompt separately preserved |
| `C:\Users\bfera\.basic-memory\config.json` | Contains machine paths and generated timestamps | `memory/config.template.json` |
| `C:\Users\bfera\.codex\config.toml` | Contains machine paths, project trust entries, native-pipe details and device identifiers | `runtimes/codex/config.toml.template` |
| `.env` and provider configuration | Values are credentials or host-specific state | Names only in `.env.example`, `tools/integrations/last30days.env.example` and `manifests/SECRETS_REQUIRED.md` |

## Intentionally excluded

| Source path or class | Component | Reason |
|---|---|---|
| `C:\Users\bfera\.basic-memory\memory.db*`, `.config\basic-memory\memory.db` | Basic Memory | Generated SQLite/index/WAL state; rebuildable from Markdown |
| `C:\Users\bfera\.codemem\mem.sqlite*` | CodeMem | Automatic session/raw-event database may contain transcripts and sensitive content; rebuildable and not canonical durable memory |
| `C:\Users\bfera\.paseo\daemon-keypair.json` | Paseo | Private runtime key material |
| `C:\Users\bfera\.paseo\push-tokens.json` | Paseo | Push/connection token material |
| `C:\Users\bfera\.paseo\cli-client-id`, `server-id` | Paseo | Device/server identity state, not Axel behavior |
| `C:\Users\bfera\.paseo\agents`, `projects`, `schedules`, `loops`, `runtime`, `uploads`, `desktop-attachments` | Paseo | Session, project, scheduling, process and attachment state; not portable identity |
| `C:\Users\bfera\.paseo\*.log` | Paseo | Runtime logs may contain prompts, paths or credentials |
| `C:\Users\bfera\.codex\auth.json` | Codex | Authentication credential |
| `C:\Users\bfera\.codex\.sandbox-secrets` | Codex | Sandbox credential material |
| `C:\Users\bfera\.codex\sessions`, `archived_sessions`, `history.jsonl`, `session_index.jsonl` | Codex | Conversation/session history, not reusable configuration |
| `C:\Users\bfera\.codex\*.sqlite*`, `memories`, `cache`, `log`, `tmp`, `mcp-oauth-locks`, `thread-writer-locks` | Codex | Generated state, caches, locks or personal history |
| `C:\Users\bfera\.config\gws\client_secret.json`, `credentials.enc`, `token_cache.json` | Google Workspace tooling | OAuth credentials and token cache |
| `C:\Users\bfera\.hermes\.env` | Hermes | Provider credential-bearing environment file; do not copy |
| `C:\Users\bfera\.config\last30days\.env` | last30days | Private tool environment file |
| `C:\Users\bfera\.config\opencode\opencode.json.backup-before-codemem-20260725-180453` | OpenCode | Historical backup contains a populated Composio credential; excluded |
| `C:\Users\bfera\AppData\Roaming\Paseo-paseo-opencode-runtime` | Paseo desktop | Electron cache, local storage, cookies/session-like state and window state |
| WSL `/home/bfera/everos-axel/.env` | WSL project | Unrelated provider credential-bearing project environment |
| WSL `/home/bfera/.config/opencode/opencode.jsonc` | WSL OpenCode | Minimal schema-only config; no Axel-specific behavior |
| `C:\Users\bfera\.mempalace`, `.openmausbot`, `.copilot`, `.cherrystudio` | Other tools | Unrelated/legacy runtime state; no current Axel dependency discovered |
| `C:\Users\bfera\Documents\axel-compound-pilot` | Separate project | Simulation pilot, not runtime identity or required recovery state |
| `C:\Users\bfera\Documents\paseo-opencode-runtime` source tree | Paseo source | Large third-party/runtime source checkout; source URL, branch, commit and version recorded instead |

## Working-tree caveat

The source repositories `paseo-codex-test` and `codemem` were dirty at
inventory time. Their current files, including untracked files intentionally
identified above, were copied into the package where relevant. No source
changes were reverted, staged or committed by this migration.

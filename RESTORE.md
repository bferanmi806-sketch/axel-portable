# Restore Axel

This procedure is deliberately profile-first. It reconstructs Axel without
depending on Paseo and without changing the existing installation.

## 1. Clone and inspect

```powershell
git clone <private-repository-url> axel-portable
Set-Location .\axel-portable
Get-Content .\manifests\SECRETS_REQUIRED.md
```

Keep the clone private. Do not place populated credentials in the repository.

## 2. Create an isolated profile

```powershell
.\scripts\bootstrap\bootstrap.ps1 -ProfileRoot "$HOME\Axel-Portable-Profile"
```

This creates a profile containing copied identity, memory sources, sanitized
runtime templates and a generated OpenCode configuration. The current global
configuration is not changed.

## 3. Restore Basic Memory source projects

Install the observed Basic Memory version or a compatible newer version, then
register the copied Markdown directories explicitly:

```powershell
basic-memory project add axel "$HOME\Axel-Portable-Profile\memory\basic-memory\axel" --local
basic-memory project add main "$HOME\Axel-Portable-Profile\memory\basic-memory\main" --local
basic-memory project add axel-project-knowledge "$HOME\Axel-Portable-Profile\memory\basic-memory\axel-project-knowledge" --local
basic-memory project default axel --local
basic-memory reindex --project axel --full
basic-memory status --project axel --json
```

If the target runtime uses an isolated Basic Memory config/profile, perform
these commands inside that profile. Do not replace an existing global config
without reviewing it first. The SQLite index and vector cache are derived from
the Markdown sources and are intentionally not part of this repository.

The `memory/axel-worktree/` directory is an alternate working copy discovered
under the Paseo test workspace. Do not register it as a second `Axel` project
until its differences from `memory/basic-memory/axel/` have been reviewed.

## 4. Build the vendored CodeMem integration

The current Windows OpenCode config uses a local CodeMem MCP build. The package
contains the current source snapshot under
`tools/integrations/codemem/`, including the uncommitted Dream Boundary work.
Install its dependencies and build it in the portable profile or in a separate
trusted checkout:

```powershell
Set-Location .\tools\integrations\codemem
corepack pnpm install --frozen-lockfile --ignore-scripts
corepack pnpm run build
Set-Location ..\..\..
```

Do not run `codemem setup` against the live configuration during recovery.
Review `opencode/config/opencode.json.template` and use the generated profile
configuration first.

## 5. Configure OpenCode

The generated profile config is at:

```text
<profile>\opencode\opencode.json
```

Use it with a disposable OpenCode profile or copy only the reviewed sections to
the chosen runtime's config. Set `COMPOSIO_API_KEY` and provider credentials in
the runtime environment. The Codemem plugin/server path must point to a built
`packages/mcp-server/dist/stdio.js`; Basic Memory, Wigolo, Context7 and Composio
are represented by sanitized templates.

The 14 local OpenCode skills are under `opencode/skills/local/`. The installed
global skills and their provenance lock are under `opencode/skills/global/`.
Copy or install them according to the selected runtime's skill discovery rules.

## 6. Configure Codex or another runtime

Use `runtimes/codex/config.toml.template` as a reviewed starting point. Do not
copy `auth.json`, session databases, browser state or project trust history.
Load `identity/AXEL.md`, `AGENTS.md`, the selected skills, and the Basic Memory
project explicitly when the runtime does not automatically discover them.

For another coding-agent runtime, the portable minimum is:

1. Load `AGENTS.md` and `identity/AXEL.md` as system/developer instructions.
2. Make the `memory/basic-memory/axel/` Markdown directory available to the Basic Memory adapter.
3. Make relevant skill directories available to the runtime.
4. Configure only the MCP servers and provider credentials that are actually needed.
5. Keep memory writes approval-gated and verify the runtime in a disposable profile.

## 7. Optional Paseo adapter

Paseo is an interface, not a prerequisite. If it is still desired, review
`runtimes/paseo/config.template.json`, install a compatible Paseo CLI/desktop
version, and provide its credentials separately. The daemon's Axel prompt is
already preserved in `identity/AXEL.md`; a future Paseo config should inject
that file's text rather than reintroducing an absolute workstation path.

## 8. Verify

```powershell
.\scripts\verify\secret-scan.ps1
.\scripts\verify\integrity.ps1
.\scripts\verify\restore-dry-run.ps1
```

These checks are local and read-only apart from the temporary restore profile
created and removed by the dry-run script. Do not report a runtime as working
until an actual disposable prompt, memory read, skill load, and MCP health check
have passed.

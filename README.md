# Axel Portable

This repository is a private, sanitized recovery package for Axel. It separates
Axel's identity, durable memory, workflows, tools and runtime adapters so Paseo
is optional rather than a single point of failure.

## Recovery model

```text
                    AXEL
       identity + memory + workflows + tools
                         |
          +--------------+--------------+
          |              |              |
       OpenCode        Paseo       another runtime
          |
     provider/model
```

`AGENTS.md` is the canonical project instruction file. `identity/AXEL.md` is the
canonical copy of Paseo's global Axel prompt. The other identity files are
human-readable derived maps and are not additional hidden instructions.

## Contents

- `identity/` contains the recovered Axel prompt and derived identity maps.
- `memory/` contains human-readable Basic Memory projects and project knowledge.
- `opencode/` contains local skills, installed global skills, commands and sanitized config templates.
- `tools/integrations/` contains the current CodeMem and Axel Improvement Engine source snapshots plus the disabled local-memory experiment.
- `runtimes/` contains OpenCode, Paseo and Codex adapter templates.
- `scripts/` contains inventory, bootstrap, restore and verification helpers.
- `manifests/` records source paths, versions, exclusions and required secrets.

## Safety

No credential store, token file, database, session history, browser cookie,
private key, populated `.env`, or runtime cache belongs in this repository.
Populate credentials separately using `.env.example` and the provider's normal
credential store. The repository is intended to remain private because the
memory files contain personal and project context even after secret removal.

## Quick restore

From PowerShell after cloning:

```powershell
Set-Location .\axel-portable
Copy-Item .env.example .env
notepad .env
.\scripts\bootstrap\bootstrap.ps1 -ProfileRoot "$HOME\Axel-Portable-Profile"
.\scripts\verify\restore-dry-run.ps1
```

The bootstrap script only writes the selected profile directory. It does not
overwrite the current OpenCode, Paseo, Codex or Basic Memory installation.
Read `RESTORE.md` before enabling any runtime adapter or registering the memory
projects globally.

## Provenance

The package was assembled from the Windows installation and the available WSL
Ubuntu installation on 2026-08-22. See `manifests/FILES.md` for the complete
inventory and `manifests/VERSIONS.md` for observed versions and source commits.

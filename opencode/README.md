# OpenCode Adapter

The current Windows OpenCode installation is represented by:

- `config/opencode.json.template` from the live sanitized configuration.
- `config/package.json` and `config/package-lock.json` from the local plugin dependency setup.
- `commands/compound.md` from the global command directory.
- `agents/design-review.md` is the read-only rendered UI critic used by Axel's frontend review gate.
- `skills/local/` from `%USERPROFILE%\.config\opencode\skills`.
- `skills/global/` from `%USERPROFILE%\.agents\skills`, with `skill-lock.json` preserving install provenance.
- `plans/` for the current Dream Mode planning artifact.
- `DESIGN-TOOLS.md` records the promoted design integrations and reference-routing workflow.

The global OpenCode `plugins/` directory is empty. The active CodeMem OpenCode
plugin is preserved with the CodeMem source snapshot under
`tools/integrations/codemem/packages/opencode-plugin/`. The experimental local
memory adapter is separate and disabled by default.

The template uses `__AXEL_PORTABLE_ROOT__` placeholders because OpenCode's
absolute command/plugin paths must be materialized for the selected checkout.

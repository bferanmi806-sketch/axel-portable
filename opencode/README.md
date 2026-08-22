# OpenCode Adapter

The current Windows OpenCode installation is represented by:

- `config/opencode.json.template` from the live sanitized configuration.
- `config/package.json` and `config/package-lock.json` from the local plugin dependency setup.
- `commands/compound.md` from the global command directory.
- `skills/local/` from `%USERPROFILE%\.config\opencode\skills`.
- `skills/global/` from `%USERPROFILE%\.agents\skills`, with `skill-lock.json` preserving install provenance.
- `plans/` for the current Dream Mode planning artifact.

No standalone agent definitions or direct OpenCode plugins were found in the
global OpenCode `agents/` or `plugins/` directories. The CodeMem OpenCode
plugin is preserved with the CodeMem source snapshot under
`tools/integrations/codemem/packages/opencode-plugin/`. The experimental local
memory adapter is separate and disabled by default.

The template uses `__AXEL_PORTABLE_ROOT__` placeholders because OpenCode's
absolute command/plugin paths must be materialized for the selected checkout.

# Secrets and Private Credentials

No populated secret is included in this repository. Supply credentials through
the selected runtime's private environment/credential store.

## Observed or required names

- `COMPOSIO_API_KEY`: OpenCode Composio remote MCP header.
- `COMPOSIO_CONSUMER_API_KEY`: optional alternate Composio client naming observed in the environment.
- `OPENAI_API_KEY`: provider/API-key path when the selected runtime requires it.
- `GOOGLE_API_KEY`: Hermes/Gemini provider path when enabled.
- `CODEMEM_OBSERVER_MODEL`, `CODEMEM_OBSERVER_RUNTIME`, `CODEMEM_RUNNER`, `CODEMEM_RUNNER_FROM`, and `CODEMEM_PLUGIN_CMD_TIMEOUT`: optional CodeMem behavior controls observed in the environment.
- `CODEMEM_PROJECT`: optional project override; review carefully because repository-derived project identity is safer.
- `AXEL_IMPROVE_ROOT`: optional local trajectory-engine root.
- `PASEO_PASSWORD`: only for a password-protected Paseo deployment.
- Optional last30days provider variables listed in `.env.example` and `tools/integrations/last30days.env.example`.
- `GITHUB_TOKEN`: optional only when `gh` is not using its own credential store; never commit it.

## Credential-bearing files excluded

- `.hermes/.env`
- `.config/last30days/.env`
- `.config/gws/client_secret.json`
- `.config/gws/credentials.enc`
- `.config/gws/token_cache.json`
- `.codex/auth.json`
- `.codex/.sandbox-secrets/`
- `.paseo/daemon-keypair.json`
- `.paseo/push-tokens.json`
- historical OpenCode backups containing populated headers
- WSL project `.env` files

During discovery, one Hermes provider environment file and one historical
OpenCode backup were found to contain populated credential material. They were
not copied. Rotate those credentials because the values were exposed during the
local inspection transcript; do not place replacements in this repository.

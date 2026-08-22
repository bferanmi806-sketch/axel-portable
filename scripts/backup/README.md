# Backup Helpers

The portable repository is the safe backup. Raw runtime state was not archived
because the available default Windows archive tooling does not provide secure
encryption and the excluded state includes credentials, tokens, cookies,
databases and conversation history.

Do not create an unencrypted archive of `.paseo`, `.codex`, `.basic-memory`,
`.codemem`, Google credential stores, provider `.env` files or browser state.
If an encrypted archive is later required, obtain an approved passphrase and
use a vetted encryption tool, then record its scope without recording the
passphrase here.

# Paseo Adapter

Paseo is an optional interface/runtime. Axel's identity and memory do not
depend on the Paseo daemon.

The current Windows daemon configuration was version `0.3.1` in the local
source checkout. `config.template.json` preserves its non-secret behavior while
replacing workstation-specific paths and the embedded prompt with a marker.
The exact prompt is preserved in `identity/AXEL.md`.

The local Paseo source checkouts were not copied into this repository. Their
source repository, branch and commit are recorded in `manifests/VERSIONS.md`.
`paseo.json` is included as a development-config reference where useful.

import type { Plugin } from "@opencode-ai/plugin"

export const UPSTREAM_REPOSITORY = "pointfish6660/opencode-memory-plugin"
export const UPSTREAM_COMMIT = "c0064bf3d83023ef4729d41aaa97eb8cf9ddf39a"
export const UPSTREAM_LICENSE = "MIT"

type UpstreamModule = {
  MemoryPlugin?: Plugin
  default?: Plugin
}

export function readUpstreamPlugin(upstream: UpstreamModule): Plugin {
  const plugin = upstream.MemoryPlugin ?? upstream.default
  if (typeof plugin !== "function") {
    throw new Error(`${UPSTREAM_REPOSITORY}@${UPSTREAM_COMMIT} does not export a plugin function`)
  }
  return plugin
}

import assert from "node:assert/strict"
import { test } from "node:test"

import type { Plugin } from "@opencode-ai/plugin"

import { readUpstreamPlugin, UPSTREAM_COMMIT, UPSTREAM_LICENSE, UPSTREAM_REPOSITORY } from "../src/upstream.js"

test("the pinned upstream source contract accepts an OpenCode plugin factory", () => {
  const fixturePlugin: Plugin = async () => ({})
  const plugin = readUpstreamPlugin({ MemoryPlugin: fixturePlugin })

  assert.equal(plugin, fixturePlugin)
  assert.equal(UPSTREAM_REPOSITORY, "pointfish6660/opencode-memory-plugin")
  assert.equal(UPSTREAM_COMMIT.length, 40)
  assert.equal(UPSTREAM_LICENSE, "MIT")
})

test("the upstream source contract rejects an incompatible module", () => {
  assert.throws(() => readUpstreamPlugin({}), /does not export a plugin function/)
})

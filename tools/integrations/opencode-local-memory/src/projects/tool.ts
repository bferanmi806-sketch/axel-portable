import { tool } from "@opencode-ai/plugin"

import type { ProjectRegistry } from "./registry.js"

export function createProjectListTool(registry: ProjectRegistry) {
  return tool({
    description: "List local memory projects, their recognized paths, repository identity, and association evidence fields. Read-only.",
    args: {},
    async execute() {
      return JSON.stringify(registry.inspect(), null, 2)
    },
  })
}

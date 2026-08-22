import { Ledger } from "../capture/ledger.js"
import { ProjectRegistry } from "../projects/registry.js"
import { UnifiedRecallPolicy } from "./policy.js"
import type { RecallSource } from "./policy.js"

const DEFAULT_BUDGET = 6_000

export type OptionalMemoryAdapter = RecallSource

export type RecallServiceOptions = {
  sources?: readonly RecallSource[]
  budget?: number
}

export class RecallService {
  readonly #ledger: Ledger
  readonly #projects: ProjectRegistry
  readonly #policy: UnifiedRecallPolicy

  constructor(ledger: Ledger, projects: ProjectRegistry, options: RecallServiceOptions = {}) {
    this.#ledger = ledger
    this.#projects = projects
    this.#policy = new UnifiedRecallPolicy([this.#ledgerSource(), ...(options.sources ?? [])], options.budget ?? DEFAULT_BUDGET)
  }

  async context(directory: string, query = ""): Promise<string> {
    try {
      const project = await this.#projects.resolve(directory)
      const result = await this.#policy.retrieve({ query, projectID: project.project.id, projectPath: directory })
      return result.context
    } catch {
      return ""
    }
  }

  #ledgerSource(): RecallSource {
    const ledger = this.#ledger
    return {
      name: "local-ledger",
      priority: 20,
      async search(request) {
        const records = request.projectID
          ? [...ledger.assertions("unscoped"), ...ledger.assertions(request.projectID)]
          : ledger.assertions()
        return records.map((assertion) => {
          const base = {
            handle: `assertion:${assertion.id}`,
            source: "local-ledger",
            scope: assertion.scope,
            content: assertion.content,
            confidence: assertion.confidence,
            status: assertion.status,
            createdAt: assertion.createdAt,
          }
          return assertion.scope === "personal" ? base : { ...base, projectID: assertion.projectID }
        })
      },
    }
  }
}

import { Ledger } from "../capture/ledger.js"
import { parseAssertions } from "./schema.js"
import type { ConsolidationRunner } from "./types.js"

const MAX_INPUT_BYTES = 24 * 1024
type FailureReporter = (error: unknown) => Promise<void>

export class ConsolidationService {
  readonly #ledger: Ledger
  readonly #runner: ConsolidationRunner
  readonly #reportFailure: FailureReporter | undefined

  constructor(ledger: Ledger, runner: ConsolidationRunner, reportFailure?: FailureReporter) {
    this.#ledger = ledger
    this.#runner = runner
    this.#reportFailure = reportFailure
  }

  async queue(sessionID: string): Promise<void> {
    await this.#ledger.queueConsolidation(sessionID)
  }

  async drainOne(): Promise<void> {
    const sessionID = this.#ledger.queuedConsolidations(1)[0]
    if (sessionID) await this.consolidate(sessionID)
  }

  async consolidate(sessionID: string): Promise<void> {
    const work = await this.#ledger.prepareConsolidation(sessionID, MAX_INPUT_BYTES)
    if (!work) return
    try {
      const result = await this.#runner.run(work.input)
      const assertions = parseAssertions(result.output, new Set(work.input.events.map((event) => event.id)))
      await this.#ledger.completeConsolidation(work.runID, result.model, assertions)
      await this.#ledger.dequeueConsolidation(sessionID)
    } catch (error) {
      await this.#ledger.failConsolidation(work.runID, "consolidation failed")
      try {
        await this.#reportFailure?.(error)
      } catch {
        // Failure reporting must not become a second failure path.
      }
    }
  }
}

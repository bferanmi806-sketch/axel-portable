import { Ledger } from "../capture/ledger.js"

export class MemoryControlService {
  readonly #ledger: Ledger

  constructor(ledger: Ledger) {
    this.#ledger = ledger
  }

  inspect(assertionID: string) {
    return this.#ledger.inspectAssertion(assertionID)
  }

  async correct(assertionID: string, content: string) {
    return this.#ledger.correctAssertion(assertionID, content)
  }

  previewForget(eventIDs: readonly string[]) {
    return this.#ledger.forgetPreview(eventIDs)
  }

  async forget(eventIDs: readonly string[], confirmed: boolean) {
    if (!confirmed) throw new Error("Forgetting requires confirmed: true after preview")
    return this.#ledger.forget(eventIDs)
  }

  async rebuild(): Promise<void> {
    await this.#ledger.rebuildDerived()
  }

  exportMarkdown(): string {
    return this.#ledger.assertions().map((assertion) => [
      `## assertion:${assertion.id}`,
      `- Scope: ${assertion.scope}`,
      `- Status: ${assertion.status}`,
      `- Evidence: ${assertion.sourceEventIDs.map((id) => `event:${id}`).join(", ")}`,
      "",
      assertion.content,
    ].join("\n")).join("\n\n")
  }
}

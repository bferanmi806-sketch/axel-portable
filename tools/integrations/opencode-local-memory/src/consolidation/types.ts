export const ASSERTION_CATEGORIES = [
  "decision",
  "preference",
  "correction",
  "project-status",
  "commitment",
  "lesson",
] as const

export type AssertionCategory = typeof ASSERTION_CATEGORIES[number]
export type AssertionScope = "project" | "personal"
export type AssertionStatus = "current" | "superseded"

export type AssertionProposal = {
  scope: AssertionScope
  category: AssertionCategory
  content: string
  confidence: number
  sourceEventIDs: readonly string[]
  supersedesID?: string
}

export type AssertionRecord = AssertionProposal & {
  id: string
  projectID: string
  status: AssertionStatus
  model: string
  createdAt: string
  runID: string
}

export type ConsolidationInput = {
  sessionID: string
  projectID: string
  events: ReadonlyArray<{
    id: string
    sequence: number
    eventType: string
    occurredAt: string
    payload: string
  }>
}

export type ConsolidationRunner = {
  run(input: ConsolidationInput): Promise<{ model: string; output: string }>
}

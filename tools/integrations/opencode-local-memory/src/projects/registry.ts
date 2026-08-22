import { randomUUID } from "node:crypto"

import { Ledger } from "../capture/ledger.js"
import { inspectWorkspace } from "./identity.js"
import type { ProjectRecord, ProjectResolution, ReconciliationPreview, WorkspaceIdentity } from "./types.js"

export class ProjectRegistry {
  readonly #ledger: Ledger

  constructor(ledger: Ledger) {
    this.#ledger = ledger
  }

  async resolve(directory: string): Promise<ProjectResolution> {
    const workspace = inspectWorkspace(directory)
    const exact = this.#ledger.findProjectByPath(workspace.workspacePath)
    if (exact) return { project: exact, evidence: "exact-path", remoteCandidates: [] }

    if (workspace.repositoryCommonDirectory) {
      const commonDirectory = this.#ledger.findProjectByCommonDirectory(workspace.repositoryCommonDirectory)
      if (commonDirectory) {
        await this.#ledger.addProjectPath(commonDirectory.id, workspace.workspacePath)
        return { project: this.#ledger.findProject(commonDirectory.id) ?? commonDirectory, evidence: "common-git-directory", remoteCandidates: [] }
      }
    }

    const remoteCandidates = workspace.repositoryRemote
      ? this.#ledger.findProjectsByRemote(workspace.repositoryRemote)
      : []
    const project = await this.#ledger.createProject({
      id: randomUUID(),
      workspace,
    })
    return { project, evidence: "new-project", remoteCandidates }
  }

  previewReconciliation(directory: string): ReconciliationPreview {
    const workspace = inspectWorkspace(directory)
    return {
      workspace,
      candidates: workspace.repositoryRemote ? this.#ledger.findProjectsByRemote(workspace.repositoryRemote) : [],
    }
  }

  async reconcile(projectID: string, directory: string): Promise<ProjectRecord> {
    const workspace = inspectWorkspace(directory)
    const target = this.#ledger.findProject(projectID)
    if (!target || target.kind !== "project") throw new Error("Project does not exist or cannot be reconciled")
    const occupied = this.#ledger.findProjectByPath(workspace.workspacePath)
    if (occupied && occupied.id !== projectID) {
      throw new Error("Workspace path already belongs to another project; inspect before reconciling")
    }
    await this.#ledger.updateProjectIdentity(projectID, workspace)
    await this.#ledger.addProjectPath(projectID, workspace.workspacePath)
    return this.#ledger.findProject(projectID) ?? target
  }

  inspect(): readonly ProjectRecord[] {
    return this.#ledger.projects()
  }
}

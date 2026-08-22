export type ProjectKind = "project" | "unscoped"

export type WorkspaceIdentity = {
  workspacePath: string
  repositoryRoot: string | null
  repositoryCommonDirectory: string | null
  repositoryRemote: string | null
}

export type ProjectRecord = {
  id: string
  kind: ProjectKind
  status: "active"
  repositoryCommonDirectory: string | null
  repositoryRemote: string | null
  createdAt: string
  updatedAt: string
  paths: readonly string[]
}

export type ProjectResolution = {
  project: ProjectRecord
  evidence: "exact-path" | "common-git-directory" | "new-project"
  remoteCandidates: readonly ProjectRecord[]
}

export type ReconciliationPreview = {
  workspace: WorkspaceIdentity
  candidates: readonly ProjectRecord[]
}

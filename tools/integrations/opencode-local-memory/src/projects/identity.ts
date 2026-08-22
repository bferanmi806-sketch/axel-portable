import { execFileSync } from "node:child_process"
import { realpathSync } from "node:fs"
import { resolve } from "node:path"

import type { WorkspaceIdentity } from "./types.js"

export function inspectWorkspace(directory: string): WorkspaceIdentity {
  const workspacePath = normalizePath(directory)
  const repositoryRoot = git(directory, ["rev-parse", "--show-toplevel"])
  if (!repositoryRoot) {
    return {
      workspacePath,
      repositoryRoot: null,
      repositoryCommonDirectory: null,
      repositoryRemote: null,
    }
  }

  const commonDirectory = git(repositoryRoot, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
  const remote = git(repositoryRoot, ["config", "--get", "remote.origin.url"])
  return {
    workspacePath,
    repositoryRoot: normalizePath(repositoryRoot),
    repositoryCommonDirectory: commonDirectory ? normalizePath(commonDirectory) : null,
    repositoryRemote: remote ? normalizeRemote(remote) : null,
  }
}

export function normalizePath(path: string): string {
  const normalize = (value: string) => {
    const separated = value.replaceAll("\\", "/").replace(/\/+$/, "")
    return process.platform === "win32" ? separated.toLocaleLowerCase() : separated
  }
  try {
    return normalize(realpathSync(path))
  } catch {
    return normalize(resolve(path))
  }
}

function git(directory: string, args: readonly string[]): string | null {
  try {
    const output = execFileSync("git", ["-C", directory, ...args], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      windowsHide: true,
    }).trim()
    return output || null
  } catch {
    return null
  }
}

function normalizeRemote(remote: string): string {
  return remote.trim().replace(/\/$/, "").replace(/\.git$/i, "").toLocaleLowerCase()
}

import { homedir } from "node:os"
import { isAbsolute, join, resolve } from "node:path"

import type { PluginOptions } from "@opencode-ai/plugin"

export type SubsystemSwitches = {
  capture: boolean
  consolidation: boolean
  injection: boolean
}

export type LocalMemoryConfig = SubsystemSwitches & {
  dataDir: string
  consolidationTimeoutMs: number
  capturePolicy: {
    excludedWorkspaces: readonly string[]
    excludedTools: readonly string[]
    excludedPaths: readonly string[]
    contentRedactions: readonly string[]
    maxTextBytes: number | undefined
  }
  issues: readonly string[]
}

export type DataLayout = {
  root: string
  database: string
  backups: string
  diagnostics: string
  references: string
}

export const SAFE_DEFAULTS: Readonly<SubsystemSwitches> = Object.freeze({
  capture: false,
  consolidation: false,
  injection: false,
})

function defaultDataDir(env: NodeJS.ProcessEnv, platform: NodeJS.Platform): string {
  if (platform === "win32") {
    return join(env.LOCALAPPDATA ?? join(homedir(), "AppData", "Local"), "Axel", "OpenCodeMemory")
  }

  return join(env.XDG_DATA_HOME ?? join(homedir(), ".local", "share"), "opencode-local-memory")
}

function environmentKey(key: keyof SubsystemSwitches): string {
  return `AXEL_OPENCODE_MEMORY_${key.toUpperCase()}`
}

function readSwitch(
  options: PluginOptions,
  key: keyof SubsystemSwitches,
  issues: string[],
  env: NodeJS.ProcessEnv,
): boolean {
  const value = options[key]
  if (value === undefined) {
    const environmentValue = env[environmentKey(key)]
    if (environmentValue === undefined) return SAFE_DEFAULTS[key]
    if (environmentValue === "true") return true
    if (environmentValue === "false") return false
    issues.push(`${environmentKey(key)} must be true or false; using false`)
    return false
  }
  if (typeof value === "boolean") return value
  issues.push(`${key} must be a boolean; using false`)
  return false
}

function readStringList(options: PluginOptions, key: string, issues: string[]): readonly string[] {
  const value = options[key]
  if (value === undefined) return []
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) return value
  issues.push(`${key} must be an array of strings; ignoring it`)
  return []
}

function readPositiveInteger(options: PluginOptions, key: string, issues: string[]): number | undefined {
  const value = options[key]
  if (value === undefined) return undefined
  if (typeof value === "number" && Number.isSafeInteger(value) && value > 0) return value
  issues.push(`${key} must be a positive safe integer; ignoring it`)
  return undefined
}

const DEFAULT_CONSOLIDATION_TIMEOUT_MS = 30_000

export function parseConfig(
  options: PluginOptions = {},
  env: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
): LocalMemoryConfig {
  const issues: string[] = []
  const configuredDataDir = options.dataDir
  let dataDir = defaultDataDir(env, platform)

  if (configuredDataDir !== undefined) {
    if (typeof configuredDataDir === "string" && isAbsolute(configuredDataDir)) {
      dataDir = resolve(configuredDataDir)
    } else {
      issues.push("dataDir must be an absolute path; using the platform default")
    }
  } else if (env.AXEL_OPENCODE_MEMORY_DATA_DIR !== undefined) {
    if (isAbsolute(env.AXEL_OPENCODE_MEMORY_DATA_DIR)) {
      dataDir = resolve(env.AXEL_OPENCODE_MEMORY_DATA_DIR)
    } else {
      issues.push("AXEL_OPENCODE_MEMORY_DATA_DIR must be an absolute path; using the platform default")
    }
  }

  const consolidationTimeoutMs = readPositiveInteger(options, "consolidationTimeoutMs", issues) ?? DEFAULT_CONSOLIDATION_TIMEOUT_MS
  return {
    capture: readSwitch(options, "capture", issues, env),
    consolidation: readSwitch(options, "consolidation", issues, env),
    injection: readSwitch(options, "injection", issues, env),
    dataDir,
    consolidationTimeoutMs,
    capturePolicy: {
      excludedWorkspaces: readStringList(options, "excludedWorkspaces", issues),
      excludedTools: readStringList(options, "excludedTools", issues),
      excludedPaths: readStringList(options, "excludedPaths", issues),
      contentRedactions: readStringList(options, "contentRedactions", issues),
      maxTextBytes: readPositiveInteger(options, "maxTextBytes", issues),
    },
    issues,
  }
}

export function getDataLayout(dataDir: string): DataLayout {
  const root = resolve(dataDir)
  return {
    root,
    database: join(root, "memory.sqlite3"),
    backups: join(root, "backups"),
    diagnostics: join(root, "diagnostics"),
    references: join(root, "references"),
  }
}

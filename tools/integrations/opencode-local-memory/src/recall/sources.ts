import { execFile } from "node:child_process"
import { constants, existsSync } from "node:fs"
import { access, readdir, readFile, realpath, stat } from "node:fs/promises"
import { dirname, join, relative, resolve, sep } from "node:path"
import { promisify } from "node:util"

import type { RecallRecord, RecallRequest, RecallSource } from "./policy.js"

const execFileAsync = promisify(execFile)
const DEFAULT_MAX_FILES = 100
const DEFAULT_MAX_FILE_BYTES = 64 * 1024
const DEFAULT_CODEMEM_LIMIT = 20

export type BasicMemoryMarkdownSourceOptions = {
  root: string
  sourceName?: string
  maxFiles?: number
  maxFileBytes?: number
}

export class BasicMemoryMarkdownSource implements RecallSource {
  readonly name: string
  readonly #root: string
  readonly #maxFiles: number
  readonly #maxFileBytes: number

  constructor(options: BasicMemoryMarkdownSourceOptions) {
    this.name = options.sourceName ?? "basic-memory"
    this.#root = resolve(options.root)
    this.#maxFiles = options.maxFiles ?? DEFAULT_MAX_FILES
    this.#maxFileBytes = options.maxFileBytes ?? DEFAULT_MAX_FILE_BYTES
    if (!Number.isSafeInteger(this.#maxFiles) || this.#maxFiles < 1) throw new Error("maxFiles must be positive")
    if (!Number.isSafeInteger(this.#maxFileBytes) || this.#maxFileBytes < 1) throw new Error("maxFileBytes must be positive")
  }

  async search(request: RecallRequest): Promise<readonly RecallRecord[]> {
    const root = await realpath(this.#root)
    const paths = await markdownFiles(root, this.#maxFiles)
    const records: RecallRecord[] = []
    for (const path of paths) {
      const resolved = await realpath(path)
      if (!isWithin(root, resolved)) continue
      const metadata = await stat(resolved)
      if (!metadata.isFile() || metadata.size > this.#maxFileBytes) continue
      const raw = await readFile(resolved, "utf8")
      const content = markdownBody(raw).trim()
      if (!content) continue
      const relativePath = relative(root, resolved)
      const personal = isPersonalPath(relativePath)
      if (!personal && !hasQueryMatch(content, request.query ?? "")) continue
      records.push({
        handle: `${this.name}:${relativePath.replaceAll(sep, "/")}`,
        source: this.name,
        scope: personal ? "personal" : "project",
        content: content.slice(0, this.#maxFileBytes),
        confidence: 0.8,
        status: "current",
        ...(personal ? {} : { projectID: request.projectID }),
        createdAt: metadata.mtime.toISOString(),
      })
    }
    return records
  }
}

export type CodememCliSourceOptions = {
  command?: readonly string[]
  sourceName?: string
  limit?: number
  timeoutMs?: number
  allProjects?: boolean
  personalProjects?: readonly string[]
  projectIDs?: Readonly<Record<string, string>>
}

export class CodememCliSource implements RecallSource {
  readonly name: string
  readonly #command: readonly string[]
  readonly #limit: number
  readonly #timeoutMs: number
  readonly #allProjects: boolean
  readonly #personalProjects: ReadonlySet<string>
  readonly #projectIDs: Readonly<Record<string, string>>

  constructor(options: CodememCliSourceOptions = {}) {
    this.name = options.sourceName ?? "codemem"
    this.#command = options.command?.length ? options.command : defaultCodememCommand()
    this.#limit = options.limit ?? DEFAULT_CODEMEM_LIMIT
    this.#timeoutMs = options.timeoutMs ?? 1_500
    this.#allProjects = options.allProjects ?? false
    this.#personalProjects = new Set(options.personalProjects ?? ["Axel"])
    this.#projectIDs = options.projectIDs ?? {}
    if (!Number.isSafeInteger(this.#limit) || this.#limit < 1) throw new Error("limit must be positive")
    if (!Number.isSafeInteger(this.#timeoutMs) || this.#timeoutMs < 1) throw new Error("timeoutMs must be positive")
  }

  async search(request: RecallRequest): Promise<readonly RecallRecord[]> {
    const query = request.query?.trim()
    const args = query ? ["search", "-n", String(this.#limit), "-j"] : ["recent", "--limit", String(this.#limit), "-j"]
    if (this.#allProjects) args.push("--all-projects")
    else if (request.projectPath) args.push("--project", request.projectPath)
    if (query) args.push(query)
    const command = this.#command[0] as string
    const commandArgs = [...this.#command.slice(1), ...args]
    const executable = isWindowsShim(command) ? "cmd.exe" : command
    const executableArgs = isWindowsShim(command)
      ? ["/d", "/c", [commandToken(command), ...commandArgs.map(quoteWindowsArg)].join(" ")]
      : commandArgs
    const output = await execFileAsync(executable, executableArgs, {
      timeout: this.#timeoutMs,
      maxBuffer: 256 * 1024,
      windowsHide: true,
    })
    const rows = JSON.parse(output.stdout) as unknown
    if (!Array.isArray(rows)) return []
    return rows.flatMap((row) => this.#record(row, request))
  }

  #record(value: unknown, request: RecallRequest): RecallRecord[] {
    if (!value || typeof value !== "object") return []
    const row = value as Record<string, unknown>
    const body = typeof row.body_text === "string" ? row.body_text.trim() : ""
    const project = typeof row.project === "string" ? row.project : ""
    const confidence = typeof row.confidence === "number" ? row.confidence : 0
    const createdAt = typeof row.created_at === "string" ? row.created_at : undefined
    const id = typeof row.id === "number" || typeof row.id === "string" ? String(row.id) : ""
    if (!body || !id || !Number.isFinite(confidence)) return []
    const personal = this.#personalProjects.has(project)
    if (!personal && this.#allProjects && this.#projectIDs[project] !== request.projectID) return []
    return [{
      handle: `${this.name}:${id}`,
      source: this.name,
      scope: personal ? "personal" : "project",
      content: body.slice(0, DEFAULT_MAX_FILE_BYTES),
      confidence,
      status: "current",
      ...(personal ? {} : { projectID: request.projectID }),
      ...(createdAt ? { createdAt } : {}),
    }]
  }
}

async function markdownFiles(root: string, maxFiles: number): Promise<string[]> {
  const found: string[] = []
  const pending = [root]
  while (pending.length > 0 && found.length < maxFiles) {
    const directory = pending.shift() as string
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = resolve(directory, entry.name)
      if (entry.isDirectory()) pending.push(path)
      else if (entry.isFile() && path.toLocaleLowerCase().endsWith(".md")) found.push(path)
      if (found.length >= maxFiles) break
    }
  }
  return found.sort()
}

function markdownBody(raw: string): string {
  return raw.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/u, "")
}

function isPersonalPath(path: string): boolean {
  const first = path.split(/[\\/]/u)[0]?.toLocaleLowerCase()
  return first === "profile" || first === "preferences"
}

function hasQueryMatch(content: string, query: string): boolean {
  const terms = query.toLocaleLowerCase().split(/\W+/u).filter((term) => term.length > 2)
  return terms.length > 0 && terms.some((term) => content.toLocaleLowerCase().includes(term))
}

function isWithin(root: string, candidate: string): boolean {
  const prefix = root.endsWith(sep) ? root : `${root}${sep}`
  return candidate === root || candidate.startsWith(prefix)
}

function isWindowsShim(command: string): boolean {
  return process.platform === "win32" && /\.(?:cmd|bat)$/iu.test(command)
}

function defaultCodememCommand(): readonly string[] {
  if (process.platform !== "win32") return ["codemem"]
  const npxScript = join(dirname(process.execPath), "node_modules", "npm", "bin", "npx-cli.js")
  return existsSync(npxScript) ? [process.execPath, npxScript, "--yes", "codemem"] : ["codemem.cmd"]
}

function quoteWindowsArg(value: string): string {
  if (/[\r\n%&|<>^!]/u.test(value)) throw new Error("codemem command argument contains unsafe shell characters")
  if (/^[a-z0-9_./:-]+$/iu.test(value)) return value
  return `^"${value.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}^"`
}

function commandToken(value: string): string {
  if (/^[a-z0-9_.-]+$/iu.test(value)) return value
  return quoteWindowsArg(value)
}

export async function assertReadableDirectory(path: string): Promise<void> {
  await access(path, constants.R_OK)
  const metadata = await stat(path)
  if (!metadata.isDirectory()) throw new Error("memory root must be a directory")
}

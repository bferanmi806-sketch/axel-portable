import { copyFileSync, writeFileSync } from "node:fs"
import { basename, join } from "node:path"

import { Ledger } from "./capture/ledger.js"

export async function createBackup(ledger: Ledger, directory: string): Promise<{ database: string; manifest: string }> {
  const stamp = new Date().toISOString().replaceAll(":", "-")
  const database = join(directory, `memory-${stamp}.sqlite3`)
  await ledger.backup(database)
  const manifest = `${database}.json`
  writeFileSync(manifest, JSON.stringify({ database: basename(database), createdAt: new Date().toISOString(), status: ledger.status() }, null, 2), { mode: 0o600 })
  return { database, manifest }
}

export function restoreBackup(source: string, destination: string): void {
  copyFileSync(source, destination, 0)
}

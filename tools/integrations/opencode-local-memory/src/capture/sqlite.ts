import { createRequire } from "node:module"
import { writeFileSync } from "node:fs"

export type SQLiteStatement = {
  run(...parameters: unknown[]): unknown
  get(...parameters: unknown[]): unknown
  all(...parameters: unknown[]): readonly unknown[]
}

export type SQLiteDatabase = {
  exec(sql: string): void
  prepare(sql: string): SQLiteStatement
  close(): void
}

type SQLiteRuntime = {
  database: SQLiteDatabase
  backup(destination: string): Promise<void>
}

const require = createRequire(import.meta.url)

export function openSQLite(path: string): SQLiteRuntime {
  if (process.versions.bun) {
    const sqlite = require("bun:sqlite") as {
      Database: new (path: string) => {
        exec(sql: string): void
        prepare(sql: string): {
          run(...parameters: unknown[]): unknown
          get(...parameters: unknown[]): unknown
          all(...parameters: unknown[]): readonly unknown[]
        }
        close(): void
        serialize(): Uint8Array
      }
    }
    const rawDatabase = new sqlite.Database(path)
    const database: SQLiteDatabase = {
      exec: (sql) => rawDatabase.exec(sql),
      prepare: (sql) => {
        const statement = rawDatabase.prepare(sql)
        return {
          run: (...parameters) => statement.run(...parameters),
          get: (...parameters) => {
            const row = statement.get(...parameters)
            return row === null ? undefined : row
          },
          all: (...parameters) => statement.all(...parameters),
        }
      },
      close: () => rawDatabase.close(),
    }
    return {
      database,
      async backup(destination) {
        rawDatabase.exec("PRAGMA wal_checkpoint(TRUNCATE)")
        writeFileSync(destination, rawDatabase.serialize())
      },
    }
  }

  const sqlite = require("node:sqlite") as {
    DatabaseSync: new (path: string) => SQLiteDatabase
    backup(database: SQLiteDatabase, destination: string): Promise<void>
  }
  const database = new sqlite.DatabaseSync(path)
  return {
    database,
    backup: (destination) => sqlite.backup(database, destination),
  }
}

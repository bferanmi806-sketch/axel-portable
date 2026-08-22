"""SQLite persistence and atomic JSONL ingestion for sanitized trajectories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from collections.abc import Iterable, Mapping
import tempfile
from typing import Any
import uuid

from .errors import LedgerError, RecordValidationError, UnsafePathError
from .models import Trajectory
from .redaction import Redactor


ENGINE_MARKER = "# Axel Improvement Engine local data"
DEFAULT_CONFIG = {
    "schema_version": 1,
    "capture": {
        "max_string_chars": 4096,
        "max_output_chars": 2048,
        "max_collection_items": 100,
        "max_depth": 8,
    },
}
MAX_JSONL_LINE_CHARS = 262_144
MAX_JSONL_FILE_BYTES = 32 * 1024 * 1024
MAX_RECORDS_PER_IMPORT = 10_000
MAX_DIAGNOSTICS_PER_IMPORT = 1_000
DIAGNOSTIC_SOURCE = "<jsonl-input>"


def _ingest_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def atomic_write_text(path: str | Path, text: str) -> None:
    """Replace a text file atomically within its destination directory."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = temporary.name
        os.replace(temporary_path, target)
        temporary_path = None
    except OSError as exc:
        raise LedgerError(f"failed to write local file: {target.name}") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


@dataclass(frozen=True)
class IngestDiagnostic:
    """Safe summary of one rejected input line."""

    source: str
    line_number: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "line_number": self.line_number,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class IngestResult:
    """Summary of an atomic ingestion attempt."""

    imported: int
    duplicates: int
    rejected: int
    diagnostics: tuple[IngestDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


def initialize_layout(root: str | Path) -> Path:
    """Create the local engine layout without overwriting operator files."""

    root_path = Path(root).expanduser()
    root_path.mkdir(parents=True, exist_ok=True)
    for relative in (
        "data",
        "skills/active",
        "skills/candidates",
        "skills/approved",
        "fixtures",
        "reports",
    ):
        (root_path / relative).mkdir(parents=True, exist_ok=True)

    gitignore = root_path / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    additions: list[str] = []
    if ENGINE_MARKER not in existing:
        additions.append(ENGINE_MARKER)
    if not any(line.strip() == "/data/" for line in existing.splitlines()):
        additions.append("/data/")
    if additions:
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        addition_text = "\n".join(additions)
        atomic_write_text(gitignore, f"{existing}{prefix}{addition_text}\n")

    config_path = root_path / "config.json"
    if not config_path.exists():
        atomic_write_text(config_path, json.dumps(DEFAULT_CONFIG, indent=2, sort_keys=True) + "\n")
    return root_path


class LedgerStore:
    """SQLite-backed store for append-only sanitized trajectory records."""

    def __init__(self, root: str | Path):
        self.root = initialize_layout(root)
        self.db_path = self.root / "data" / "ledger.sqlite3"
        try:
            self.connection = sqlite3.connect(str(self.db_path))
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self._migrate()
        except sqlite3.Error as exc:
            raise LedgerError(f"failed to open ledger: {exc}") from exc

    @classmethod
    def open(cls, root: str | Path) -> "LedgerStore":
        """Open or create a ledger under ``root``."""

        return cls(root)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "LedgerStore":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def _migrate(self) -> None:
        try:
            with self.connection:
                self.connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    )
                    """
                )
                applied = self.connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 1"
                ).fetchone()
                if applied is None:
                    try:
                        self.connection.executescript(
                            """
                        BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS trajectories (
                            id TEXT PRIMARY KEY,
                            schema_version INTEGER NOT NULL,
                            host TEXT NOT NULL,
                            host_version TEXT,
                            project_id TEXT NOT NULL,
                            session_id TEXT NOT NULL,
                            task TEXT NOT NULL,
                            outcome_status TEXT NOT NULL,
                            evaluation_status TEXT NOT NULL,
                            evaluation_score REAL,
                            created_at TEXT NOT NULL,
                            ingested_at TEXT NOT NULL,
                            payload_digest TEXT NOT NULL,
                            payload_json TEXT NOT NULL
                        );

                        CREATE INDEX IF NOT EXISTS idx_trajectories_project
                            ON trajectories(project_id, created_at);
                        CREATE INDEX IF NOT EXISTS idx_trajectories_status
                            ON trajectories(outcome_status, evaluation_status);

                        CREATE TABLE IF NOT EXISTS trajectory_events (
                            id TEXT PRIMARY KEY,
                            trajectory_id TEXT NOT NULL,
                            ordinal INTEGER NOT NULL,
                            event_type TEXT NOT NULL,
                            tool_name TEXT,
                            status TEXT,
                            duration_ms INTEGER,
                            payload_json TEXT NOT NULL,
                            created_at TEXT,
                            FOREIGN KEY (trajectory_id) REFERENCES trajectories(id) ON DELETE CASCADE,
                            UNIQUE (trajectory_id, ordinal)
                        );

                        CREATE INDEX IF NOT EXISTS idx_events_trajectory
                            ON trajectory_events(trajectory_id, ordinal);

                        CREATE TABLE IF NOT EXISTS outcome_evidence (
                            trajectory_id TEXT PRIMARY KEY,
                            summary TEXT NOT NULL,
                            user_correction TEXT,
                            evaluation_json TEXT NOT NULL,
                            FOREIGN KEY (trajectory_id) REFERENCES trajectories(id) ON DELETE CASCADE
                        );

                        CREATE TABLE IF NOT EXISTS ingestion_diagnostics (
                            id TEXT PRIMARY KEY,
                            source TEXT NOT NULL,
                            line_number INTEGER NOT NULL,
                            reason TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        );

                        INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                        VALUES (1, CURRENT_TIMESTAMP);
                        COMMIT;
                        """
                        )
                    except sqlite3.Error:
                        self.connection.rollback()
                        raise
                applied = self.connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 2"
                ).fetchone()
                if applied is None:
                    try:
                        self.connection.executescript(
                            """
                        BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS diagnoses (
                            id TEXT PRIMARY KEY,
                            schema_version INTEGER NOT NULL,
                            signature TEXT NOT NULL UNIQUE,
                            diagnosis_class TEXT NOT NULL,
                            target TEXT NOT NULL,
                            target_confidence REAL NOT NULL,
                            recurrence_count INTEGER NOT NULL,
                            promotable INTEGER NOT NULL,
                            status TEXT NOT NULL,
                            rationale TEXT NOT NULL,
                            trajectory_ids_json TEXT NOT NULL,
                            evidence_ids_json TEXT NOT NULL,
                            event_ids_json TEXT NOT NULL,
                            provider_json TEXT,
                            payload_digest TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );

                        CREATE INDEX IF NOT EXISTS idx_diagnoses_status
                            ON diagnoses(status, diagnosis_class);

                        INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                        VALUES (2, CURRENT_TIMESTAMP);
                        COMMIT;
                        """
                        )
                    except sqlite3.Error:
                        self.connection.rollback()
                        raise
        except sqlite3.Error as exc:
            raise LedgerError(f"ledger migration failed: {exc}") from exc

    def ingest_jsonl(self, path: str | Path, redactor: Redactor | None = None) -> IngestResult:
        """Validate an entire JSONL file, then import it atomically."""

        source_path = Path(path)
        if not source_path.is_file():
            raise LedgerError(f"input file does not exist: {source_path}")

        records: list[Trajectory] = []
        diagnostics: list[IngestDiagnostic] = []
        try:
            if source_path.stat().st_size > MAX_JSONL_FILE_BYTES:
                diagnostic = IngestDiagnostic(
                    DIAGNOSTIC_SOURCE,
                    0,
                    "input file exceeds size limit",
                )
                self._save_diagnostics((diagnostic,))
                return IngestResult(0, 0, 1, (diagnostic,))
            input_stream = source_path.open("r", encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise LedgerError(f"failed to read input file: {source_path}") from exc

        base_redactor = redactor or Redactor.from_environment()
        try:
            with input_stream:
                for line_number, line in enumerate(input_stream, start=1):
                    if not line.strip():
                        continue
                    if len(line) > MAX_JSONL_LINE_CHARS:
                        diagnostics.append(
                            IngestDiagnostic(DIAGNOSTIC_SOURCE, line_number, "input line exceeds size limit")
                        )
                        if len(diagnostics) >= MAX_DIAGNOSTICS_PER_IMPORT:
                            break
                        continue
                    try:
                        raw = json.loads(line)
                        if not isinstance(raw, Mapping):
                            raise RecordValidationError("trajectory must be an object")
                        records.append(Trajectory.from_mapping(raw, base_redactor.fork()))
                        if len(records) > MAX_RECORDS_PER_IMPORT:
                            diagnostics.append(
                                IngestDiagnostic(
                                    DIAGNOSTIC_SOURCE,
                                    line_number,
                                    "input file exceeds record limit",
                                )
                            )
                            break
                    except json.JSONDecodeError:
                        diagnostics.append(
                            IngestDiagnostic(DIAGNOSTIC_SOURCE, line_number, "invalid JSON")
                        )
                    except UnsafePathError as exc:
                        diagnostics.append(
                            IngestDiagnostic(DIAGNOSTIC_SOURCE, line_number, str(exc)[:500])
                        )
                    except RecordValidationError as exc:
                        diagnostics.append(
                            IngestDiagnostic(DIAGNOSTIC_SOURCE, line_number, str(exc)[:500])
                        )
                    except (TypeError, ValueError, OverflowError, RecursionError):
                        diagnostics.append(
                            IngestDiagnostic(DIAGNOSTIC_SOURCE, line_number, "invalid trajectory record")
                        )
                    if len(diagnostics) >= MAX_DIAGNOSTICS_PER_IMPORT:
                        break
        except UnicodeError:
            diagnostics.append(IngestDiagnostic(DIAGNOSTIC_SOURCE, 0, "input is not valid UTF-8"))

        if diagnostics:
            self._save_diagnostics(diagnostics)
            return IngestResult(0, 0, len(diagnostics), tuple(diagnostics))
        return self.ingest_records(records)

    def ingest_records(self, records: Iterable[Trajectory]) -> IngestResult:
        """Atomically insert validated records and ignore exact duplicates."""

        unique: list[Trajectory] = []
        seen: dict[str, str] = {}
        diagnostics: list[IngestDiagnostic] = []
        duplicate_count = 0
        for trajectory in records:
            digest = trajectory.digest()
            previous = seen.get(trajectory.id)
            if previous is not None:
                if previous == digest:
                    duplicate_count += 1
                    continue
                diagnostics.append(
                    IngestDiagnostic("records", 0, "conflicting duplicate trajectory ID")
                )
                continue
            seen[trajectory.id] = digest
            unique.append(trajectory)

        seen_event_ids: set[str] = set()
        for trajectory in unique:
            for event in trajectory.events:
                if event.id in seen_event_ids:
                    diagnostics.append(IngestDiagnostic("records", 0, "conflicting event ID"))
                seen_event_ids.add(event.id)

        existing: dict[str, str] = {}
        for trajectory in unique:
            row = self.connection.execute(
                "SELECT payload_digest FROM trajectories WHERE id = ?", (trajectory.id,)
            ).fetchone()
            if row is not None:
                existing[trajectory.id] = str(row[0])
                if existing[trajectory.id] != trajectory.digest():
                    diagnostics.append(
                        IngestDiagnostic(
                            "records",
                            0,
                            "conflicting existing trajectory ID",
                        )
                    )

        for trajectory in unique:
            if trajectory.id in existing:
                continue
            for event in trajectory.events:
                row = self.connection.execute(
                    "SELECT 1 FROM trajectory_events WHERE id = ?", (event.id,)
                ).fetchone()
                if row is not None:
                    diagnostics.append(IngestDiagnostic("records", 0, "conflicting event ID"))
                    break

        if diagnostics:
            self._save_diagnostics(diagnostics)
            return IngestResult(0, 0, len(diagnostics), tuple(diagnostics))

        imported = 0
        try:
            with self.connection:
                for trajectory in unique:
                    if trajectory.id in existing:
                        duplicate_count += 1
                        continue
                    payload = trajectory.to_dict()
                    payload_json = trajectory.canonical_json()
                    created_at = trajectory.created_at or _ingest_timestamp()
                    self.connection.execute(
                        """
                        INSERT INTO trajectories(
                            id, schema_version, host, host_version, project_id, session_id,
                            task, outcome_status, evaluation_status, evaluation_score,
                            created_at, ingested_at, payload_digest, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            trajectory.id,
                            trajectory.schema_version,
                            trajectory.host,
                            trajectory.host_version,
                            trajectory.project_id,
                            trajectory.session_id,
                            trajectory.task,
                            trajectory.outcome_status,
                            trajectory.evaluation.status,
                            trajectory.evaluation.score,
                            created_at,
                            _ingest_timestamp(),
                            trajectory.digest(),
                            payload_json,
                        ),
                    )
                    for ordinal, event in enumerate(trajectory.events):
                        event_payload = {
                            "input": event.input,
                            "output": event.output,
                        }
                        self.connection.execute(
                            """
                            INSERT INTO trajectory_events(
                                id, trajectory_id, ordinal, event_type, tool_name,
                                status, duration_ms, payload_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                event.id,
                                trajectory.id,
                                ordinal,
                                event.event_type,
                                event.tool_name,
                                event.status,
                                event.duration_ms,
                                _canonical_json(event_payload),
                                event.created_at,
                            ),
                        )
                    self.connection.execute(
                        """
                        INSERT INTO outcome_evidence(
                            trajectory_id, summary, user_correction, evaluation_json
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            trajectory.id,
                            trajectory.outcome_summary,
                            trajectory.user_correction,
                            _canonical_json(trajectory.evaluation.to_dict()),
                        ),
                    )
                    imported += 1
        except sqlite3.Error as exc:
            raise LedgerError("trajectory import failed; transaction was rolled back") from exc

        return IngestResult(imported, duplicate_count, 0)

    def _save_diagnostics(self, diagnostics: Iterable[IngestDiagnostic]) -> None:
        try:
            with self.connection:
                self.connection.executemany(
                    """
                    INSERT INTO ingestion_diagnostics(id, source, line_number, reason, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            str(uuid.uuid4()),
                            item.source,
                            item.line_number,
                            item.reason[:500],
                            _ingest_timestamp(),
                        )
                        for item in diagnostics
                    ],
                )
        except sqlite3.Error as exc:
            raise LedgerError("failed to save ingestion diagnostics") from exc

    def export_records(self) -> list[dict[str, Any]]:
        """Return sanitized trajectories in stable export order."""

        rows = self.connection.execute(
            "SELECT payload_json FROM trajectories ORDER BY created_at, id"
        ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def save_diagnoses(
        self,
        diagnoses: Iterable[Mapping[str, Any]],
        *,
        reconcile: bool = False,
    ) -> dict[str, int]:
        """Atomically upsert deterministic diagnosis records by signature."""

        prepared: list[tuple[Any, ...]] = []
        now = _ingest_timestamp()
        for diagnosis in diagnoses:
            if not isinstance(diagnosis, Mapping):
                raise LedgerError("diagnosis must be an object")
            required = ("id", "signature", "diagnosis_class", "target", "status", "rationale")
            if any(not isinstance(diagnosis.get(key), str) or not str(diagnosis[key]).strip() for key in required):
                raise LedgerError("diagnosis is missing required text fields")
            from .diagnose import DIAGNOSIS_STATUSES, validate_provider_result
            if diagnosis["diagnosis_class"] not in {
                "memory",
                "skill",
                "routing",
                "validator",
                "playbook",
                "tool-failure",
                "recovery-procedure",
                "template",
                "one-off-incident",
            }:
                raise LedgerError("diagnosis class is unsupported")
            if diagnosis["status"] not in DIAGNOSIS_STATUSES:
                raise LedgerError("diagnosis status is unsupported")
            trajectory_ids = diagnosis.get("trajectory_ids", [])
            evidence_ids = diagnosis.get("evidence_ids", [])
            event_ids = diagnosis.get("event_ids", [])
            if not all(isinstance(value, list) and all(isinstance(item, str) for item in value) for value in (trajectory_ids, evidence_ids, event_ids)):
                raise LedgerError("diagnosis evidence fields must be string lists")
            confidence = diagnosis.get("target_confidence")
            recurrence = diagnosis.get("recurrence_count")
            promotable = diagnosis.get("promotable")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
                raise LedgerError("diagnosis target confidence is invalid")
            if isinstance(recurrence, bool) or not isinstance(recurrence, int) or recurrence < 1:
                raise LedgerError("diagnosis recurrence count is invalid")
            if not isinstance(promotable, bool):
                raise LedgerError("diagnosis promotable value is invalid")
            provider = diagnosis.get("provider_assessment")
            if provider is not None and not isinstance(provider, dict):
                raise LedgerError("diagnosis provider assessment is invalid")
            if provider is not None:
                try:
                    provider = validate_provider_result(provider)
                except Exception as exc:
                    raise LedgerError("diagnosis provider assessment is invalid") from exc
            payload = dict(diagnosis)
            payload_digest = hashlib.sha256(
                json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            prepared.append(
                (
                    diagnosis["id"],
                    1,
                    diagnosis["signature"],
                    diagnosis["diagnosis_class"],
                    diagnosis["target"],
                    float(confidence),
                    recurrence,
                    int(promotable),
                    diagnosis["status"],
                    diagnosis["rationale"],
                    json.dumps(trajectory_ids, ensure_ascii=True, sort_keys=True),
                    json.dumps(evidence_ids, ensure_ascii=True, sort_keys=True),
                    json.dumps(event_ids, ensure_ascii=True, sort_keys=True),
                    json.dumps(provider, ensure_ascii=True, sort_keys=True) if provider is not None else None,
                    payload_digest,
                    now,
                    now,
                )
            )
        try:
            with self.connection:
                if reconcile:
                    signatures = [row[2] for row in prepared]
                    if signatures:
                        placeholders = ", ".join("?" for _ in signatures)
                        self.connection.execute(
                            f"UPDATE diagnoses SET status = 'retired', promotable = 0, updated_at = ? WHERE signature NOT IN ({placeholders})",
                            (now, *signatures),
                        )
                    else:
                        self.connection.execute(
                            "UPDATE diagnoses SET status = 'retired', promotable = 0, updated_at = ?",
                            (now,),
                        )
                for row in prepared:
                    self.connection.execute(
                        """
                        INSERT INTO diagnoses(
                            id, schema_version, signature, diagnosis_class, target,
                            target_confidence, recurrence_count, promotable, status,
                            rationale, trajectory_ids_json, evidence_ids_json,
                            event_ids_json, provider_json, payload_digest,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(signature) DO UPDATE SET
                            id = excluded.id,
                            schema_version = excluded.schema_version,
                            diagnosis_class = excluded.diagnosis_class,
                            target = excluded.target,
                            target_confidence = excluded.target_confidence,
                            recurrence_count = excluded.recurrence_count,
                            promotable = excluded.promotable,
                            status = excluded.status,
                            rationale = excluded.rationale,
                            trajectory_ids_json = excluded.trajectory_ids_json,
                            evidence_ids_json = excluded.evidence_ids_json,
                            event_ids_json = excluded.event_ids_json,
                            provider_json = excluded.provider_json,
                            payload_digest = excluded.payload_digest,
                            updated_at = excluded.updated_at
                        """,
                        row,
                    )
        except sqlite3.Error as exc:
            raise LedgerError("diagnosis save failed; transaction was rolled back") from exc
        return {"saved": len(prepared)}

    def export_diagnoses(self, unresolved_only: bool = False) -> list[dict[str, Any]]:
        """Return persisted diagnoses, optionally excluding eligible records."""

        query = "SELECT * FROM diagnoses"
        parameters: tuple[Any, ...] = ()
        if unresolved_only:
            query += " WHERE status <> 'retired' AND (status <> 'eligible' OR promotable = 0)"
        query += " ORDER BY diagnosis_class, signature"
        rows = self.connection.execute(query, parameters).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "id": row["id"],
                    "signature": row["signature"],
                    "diagnosis_class": row["diagnosis_class"],
                    "class": row["diagnosis_class"],
                    "target": row["target"],
                    "target_confidence": row["target_confidence"],
                    "confidence": row["target_confidence"],
                    "recurrence_count": row["recurrence_count"],
                    "promotable": bool(row["promotable"]),
                    "status": row["status"],
                    "rationale": row["rationale"],
                    "trajectory_ids": json.loads(row["trajectory_ids_json"]),
                    "evidence_ids": json.loads(row["evidence_ids_json"]),
                    "event_ids": json.loads(row["event_ids_json"]),
                    "provider_assessment": json.loads(row["provider_json"]) if row["provider_json"] else None,
                }
            )
        return result

    def status(self) -> dict[str, Any]:
        """Return counts useful for CLI health checks."""

        trajectory_count = self.connection.execute("SELECT COUNT(*) FROM trajectories").fetchone()[0]
        event_count = self.connection.execute("SELECT COUNT(*) FROM trajectory_events").fetchone()[0]
        diagnostic_count = self.connection.execute(
            "SELECT COUNT(*) FROM ingestion_diagnostics"
        ).fetchone()[0]
        evaluated_count = self.connection.execute(
            "SELECT COUNT(*) FROM trajectories WHERE evaluation_status = 'evaluated'"
        ).fetchone()[0]
        return {
            "root": str(self.root),
            "database": str(self.db_path),
            "schema_version": 2,
            "trajectories": int(trajectory_count),
            "events": int(event_count),
            "evaluated_trajectories": int(evaluated_count),
            "ingestion_diagnostics": int(diagnostic_count),
            "diagnoses": int(self.connection.execute("SELECT COUNT(*) FROM diagnoses").fetchone()[0]),
            "unresolved_diagnoses": int(self.connection.execute("SELECT COUNT(*) FROM diagnoses WHERE status <> 'retired' AND (status <> 'eligible' OR promotable = 0)").fetchone()[0]),
        }

    def recent_diagnostics(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent safe ingestion diagnostics."""

        rows = self.connection.execute(
            """
            SELECT source, line_number, reason, created_at
            FROM ingestion_diagnostics
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

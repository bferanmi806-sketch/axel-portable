"""Integration tests for SQLite initialization and atomic ingestion."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from axel_improve.store import LedgerStore


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "reconstructed.jsonl"


class StoreTests(unittest.TestCase):
    def test_twenty_fixtures_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with LedgerStore.open(temporary) as store:
                first = store.ingest_jsonl(FIXTURE_PATH)
                second = store.ingest_jsonl(FIXTURE_PATH)
                status = store.status()

            self.assertEqual(first.imported, 20)
            self.assertEqual(first.rejected, 0)
            self.assertEqual(second.imported, 0)
            self.assertEqual(second.duplicates, 20)
            self.assertEqual(status["trajectories"], 20)
            self.assertEqual(status["events"], 58)
            self.assertEqual(status["evaluated_trajectories"], 19)

    def test_persisted_fixture_data_contains_no_secret_or_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with LedgerStore.open(temporary) as store:
                result = store.ingest_jsonl(FIXTURE_PATH)
                exported = store.export_records()

            self.assertEqual(result.rejected, 0)
            exported_text = json.dumps(exported, ensure_ascii=True)
            self.assertNotIn("sk-test-secret-123456789", exported_text)
            self.assertNotIn(r"C:\\Users\\bfera\\.env", exported_text)
            self.assertIn("[REDACTED:sensitive field]", exported_text)
            self.assertIn("[REDACTED:credential path]", exported_text)

    def test_malformed_file_is_atomic_and_diagnostic_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "bad.jsonl"
            valid_line = FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[0]
            input_path.write_text(valid_line + "\nnot-json\n", encoding="utf-8")

            with LedgerStore.open(temporary) as store:
                result = store.ingest_jsonl(input_path)
                status = store.status()
                diagnostics = store.recent_diagnostics()

            self.assertEqual(result.imported, 0)
            self.assertEqual(result.rejected, 1)
            self.assertEqual(status["trajectories"], 0)
            self.assertEqual(status["ingestion_diagnostics"], 1)
            self.assertEqual(diagnostics[0]["reason"], "invalid JSON")

    def test_unsafe_path_file_is_rejected_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "unsafe.jsonl"
            record = json.loads(FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[0])
            record["events"][1]["input"]["path"] = "../../outside.txt"
            input_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with LedgerStore.open(temporary) as store:
                result = store.ingest_jsonl(input_path)
                status = store.status()

            self.assertEqual(result.imported, 0)
            self.assertEqual(result.rejected, 1)
            self.assertEqual(status["trajectories"], 0)
            self.assertIn("path traversal", result.diagnostics[0].reason)

    def test_missing_created_at_remains_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = json.loads(FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[0])
            record.pop("created_at")
            input_path = Path(temporary) / "missing-time.jsonl"
            input_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with LedgerStore.open(temporary) as store:
                first = store.ingest_jsonl(input_path)
                second = store.ingest_jsonl(input_path)

            self.assertEqual(first.imported, 1)
            self.assertEqual(second.duplicates, 1)
            self.assertEqual(second.rejected, 0)

    def test_invalid_types_and_schema_values_are_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "invalid-types.jsonl"
            record = json.loads(FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[0])
            record["schema_version"] = "Cookie: sessionid=secret"
            second = json.loads(FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[1])
            second["outcome"]["status"] = []
            input_path.write_text(
                json.dumps(record) + "\n" + json.dumps(second) + "\n", encoding="utf-8"
            )

            with LedgerStore.open(temporary) as store:
                result = store.ingest_jsonl(input_path)
                diagnostics = store.recent_diagnostics()

            self.assertEqual(result.imported, 0)
            self.assertEqual(result.rejected, 2)
            self.assertTrue(all("sessionid=secret" not in item["reason"] for item in diagnostics))

    def test_invalid_evaluation_shape_is_a_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "invalid-evaluation.jsonl"
            record = json.loads(FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[0])
            record["outcome"]["evaluation"] = []
            input_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with LedgerStore.open(temporary) as store:
                result = store.ingest_jsonl(input_path)

            self.assertEqual(result.imported, 0)
            self.assertEqual(result.rejected, 1)
            self.assertIn("evaluation must be an object", result.diagnostics[0].reason)

    def test_diagnostics_do_not_store_input_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / ".env"
            input_path.write_text("not-json\n", encoding="utf-8")

            with LedgerStore.open(temporary) as store:
                result = store.ingest_jsonl(input_path)
                diagnostics = store.recent_diagnostics()

            self.assertEqual(result.rejected, 1)
            self.assertEqual(diagnostics[0]["source"], "<jsonl-input>")
            self.assertNotIn(".env", json.dumps(diagnostics))

    def test_event_ids_cannot_collide_across_trajectories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            records = [
                json.loads(FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[0]),
                json.loads(FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[1]),
            ]
            records[1]["events"][0]["id"] = records[0]["events"][0]["id"]
            input_path = Path(temporary) / "event-collision.jsonl"
            input_path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")

            with LedgerStore.open(temporary) as store:
                result = store.ingest_jsonl(input_path)
                status = store.status()

            self.assertEqual(result.imported, 0)
            self.assertEqual(result.rejected, 1)
            self.assertEqual(status["trajectories"], 0)

    def test_existing_gitignore_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            gitignore = Path(temporary) / ".gitignore"
            gitignore.write_text("custom-rule\n", encoding="utf-8")

            with LedgerStore.open(temporary):
                pass

            content = gitignore.read_text(encoding="utf-8")
            self.assertIn("custom-rule", content)
            self.assertIn("/data/", content)

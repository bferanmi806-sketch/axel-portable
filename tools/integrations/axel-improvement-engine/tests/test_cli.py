"""CLI integration tests for initialization, ingestion, status, and export."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from axel_improve.evaluate import EvaluationConfig
from axel_improve.candidates import provenance_digest
from axel_improve.replay import load_replay_suite


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "reconstructed.jsonl"


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        source_path = str(ROOT / "src")
        environment["PYTHONPATH"] = source_path + os.pathsep + environment.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "axel_improve", *args],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cli_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            initialized = self.run_cli("init", "--root", str(root), cwd=ROOT)
            ingested = self.run_cli(
                "ingest", "--root", str(root), "--input", str(FIXTURE_PATH), cwd=ROOT
            )
            status = self.run_cli("status", "--root", str(root), cwd=ROOT)
            exported = self.run_cli("export", "--root", str(root), cwd=ROOT)

            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            self.assertEqual(ingested.returncode, 0, ingested.stderr)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertEqual(json.loads(status.stdout)["trajectories"], 20)
            self.assertEqual(len(exported.stdout.splitlines()), 20)

    def test_cli_returns_nonzero_for_rejected_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            bad_input = Path(temporary) / "bad.jsonl"
            bad_input.write_text("not-json\n", encoding="utf-8")

            result = self.run_cli(
                "ingest", "--root", str(root), "--input", str(bad_input), cwd=ROOT
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["rejected"], 1)

    def test_cli_diagnoses_and_lists_unresolved_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            self.assertEqual(
                self.run_cli("ingest", "--root", str(root), "--input", str(FIXTURE_PATH), cwd=ROOT).returncode,
                0,
            )
            diagnosed = self.run_cli("diagnose", "--root", str(root), cwd=ROOT)
            unresolved = self.run_cli("diagnoses", "--root", str(root), "--unresolved", cwd=ROOT)

            self.assertEqual(diagnosed.returncode, 0, diagnosed.stderr)
            self.assertEqual(unresolved.returncode, 0, unresolved.stderr)
            self.assertGreaterEqual(len(json.loads(diagnosed.stdout)["diagnoses"]), 4)
            self.assertTrue(all(item["status"] != "eligible" for item in json.loads(unresolved.stdout)))

    def test_cli_compounds_fixture_and_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            ingested = self.run_cli("ingest", "--root", str(root), "--input", str(FIXTURE_PATH), cwd=ROOT)
            compounded = self.run_cli("compound", "--root", str(root), "--seed", "11", cwd=ROOT)

            self.assertEqual(ingested.returncode, 0, ingested.stderr)
            self.assertEqual(compounded.returncode, 0, compounded.stderr)
            summary = json.loads(compounded.stdout)
            report = json.loads(Path(summary["json_report"]).read_text(encoding="utf-8"))
            self.assertEqual(report["trajectory_count"], 20)
            self.assertTrue(report["candidates"])
            self.assertTrue(Path(summary["markdown_report"]).is_file())

    def test_cli_builds_inspects_and_runs_replay_suite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            ingested = self.run_cli(
                "ingest", "--root", str(root), "--input", str(FIXTURE_PATH), cwd=ROOT
            )
            built = self.run_cli(
                "replay-build",
                "--root",
                str(root),
                "--seed",
                "11",
                cwd=ROOT,
            )
            built_payload = json.loads(built.stdout)
            suite_path = Path(built_payload["output"])
            inspected = self.run_cli("replay-inspect", "--suite", str(suite_path), cwd=ROOT)
            ran = self.run_cli("replay-run", "--suite", str(suite_path), cwd=ROOT)

            self.assertEqual(ingested.returncode, 0, ingested.stderr)
            self.assertEqual(built.returncode, 0, built.stderr)
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertEqual(json.loads(inspected.stdout)["cases"], 18)
            run_payload = json.loads(ran.stdout)
            self.assertEqual(run_payload["passed"], 18)
            self.assertEqual(run_payload["failed"], 0)
            self.assertEqual(run_payload["incomplete"], 0)

    def test_cli_evaluates_candidate_and_records_optional_champion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            candidate_results_path = Path(temporary) / "candidate-results.json"
            champion_path = Path(temporary) / "champion.json"
            self.assertEqual(
                self.run_cli("ingest", "--root", str(root), "--input", str(FIXTURE_PATH), cwd=ROOT).returncode,
                0,
            )
            built = self.run_cli("replay-build", "--root", str(root), "--seed", "11", cwd=ROOT)
            suite_path = Path(json.loads(built.stdout)["output"])
            suite_payload = json.loads(suite_path.read_text(encoding="utf-8"))
            suite = load_replay_suite(suite_path)
            replayed = self.run_cli("replay-run", "--suite", str(suite_path), cwd=ROOT)
            candidate_payload = json.loads(replayed.stdout)
            improved = next(
                result for result in candidate_payload["results"] if result["evidence"]["outcome_status"] != "success"
            )
            improved["status"] = "passed"
            improved["reason"] = "candidate improvement"
            improved["evidence"]["outcome_status"] = "success"
            improved["evidence"]["score"] = 1.0
            improved_case = next(case for case in suite.cases if case.id == improved["case_id"])
            improved["result_digest"] = hashlib.sha256(
                json.dumps(
                    {
                        "case_id": improved_case.id,
                        "case_digest": improved_case.digest(),
                        "status": improved["status"],
                        "reason": improved["reason"],
                        "evidence": improved["evidence"],
                        "budget": improved["budget"],
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            candidate_asset = root / "skills" / "candidates" / "candidate-cli"
            candidate_asset.mkdir()
            (root / "skills" / "candidates" / ".axel-candidate-root").write_bytes(
                b"Axel Improvement Engine candidate root\n"
            )
            candidate_content = "---\nname: candidate-cli\n---\n\n# Candidate\n"
            (candidate_asset / "SKILL.md").write_bytes(candidate_content.encode("utf-8"))
            candidate_digest = hashlib.sha256((candidate_asset / "SKILL.md").read_bytes()).hexdigest()
            candidate_diff = b"candidate diff\n"
            (candidate_asset / "change.diff").write_bytes(candidate_diff)
            provenance = {
                "schema_version": 1,
                "candidate_id": "candidate-cli",
                "candidate_digest": candidate_digest,
                "diff_digest": hashlib.sha256(candidate_diff).hexdigest(),
                "target": None,
                "parent_digest": None,
                "new_skill_name": "candidate-cli",
                "evaluation_rules": [{"id": "replay-status", "type": "deterministic"}],
                "status": "proposed",
            }
            provenance["provenance_digest"] = provenance_digest(provenance)
            (candidate_asset / "provenance.json").write_bytes(json.dumps(provenance).encode("utf-8"))
            config = EvaluationConfig()
            config_digest = hashlib.sha256(
                json.dumps(config.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            candidate_results_path.write_text(
                json.dumps(
                    {
                        "candidate_id": "candidate-cli",
                        "candidate_digest": candidate_digest,
                        "suite_id": suite_payload["id"],
                        "seed": suite_payload["seed"],
                        "runner_version": config.runner_version,
                        "config_digest": config_digest,
                        "asset_reference": str(candidate_asset.resolve()),
                        "results": {item["case_id"]: item for item in candidate_payload["results"]},
                    }
                ),
                encoding="utf-8",
            )

            evaluated = self.run_cli(
                "evaluate",
                "--root",
                str(root),
                "--suite",
                str(suite_path),
                "--candidate-id",
                "candidate-cli",
                "--baseline-digest",
                "baseline-cli",
                "--candidate-digest",
                candidate_digest,
                "--candidate-asset",
                str(candidate_asset),
                "--candidate-results",
                str(candidate_results_path),
                "--champion-registry",
                str(champion_path),
                cwd=ROOT,
            )

            self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
            payload = json.loads(evaluated.stdout)
            self.assertEqual(payload["status"], "eligible")
            self.assertEqual(payload["champion"]["candidate_id"], "candidate-cli")
            candidate_provenance = json.loads(
                (candidate_asset / "provenance.json").read_text(encoding="utf-8")
            )
            self.assertEqual(candidate_provenance["status"], "tested")
            self.assertFalse((root / "skills" / "approved").exists() and any((root / "skills" / "approved").iterdir()))

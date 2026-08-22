"""Tests for deterministic batch compounding and improvement reporting."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from axel_improve.candidates import propose_skill_candidate, transition_candidate
from axel_improve.compound import (
    CompoundConfig,
    CompoundError,
    CompoundReport,
    compound_trajectories,
    write_compound_report,
)
from axel_improve.diagnose import Diagnosis
from axel_improve.models import Trajectory
from axel_improve.promotion import PromotionError, promote_candidate, rollback_candidate
from axel_improve.retrieval import retrieve_approved_assets
from axel_improve.skills import SkillBank
from axel_improve.store import LedgerStore, initialize_layout


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "reconstructed.jsonl"


def fixture_records() -> list[dict]:
    return [json.loads(line) for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines()]


def _custom_diagnosis(identifier: str, target: str, rationale: str) -> Diagnosis:
    return Diagnosis(
        id=identifier,
        signature=f"skill|{target}|{identifier}",
        diagnosis_class="skill",
        target=target,
        target_confidence=0.9,
        recurrence_count=2,
        promotable=True,
        status="eligible",
        rationale=rationale,
        trajectory_ids=("fixture-001", "fixture-002"),
        evidence_ids=("fixture-001", "fixture-002"),
        event_ids=("fixture-001-e1", "fixture-002-e1"),
    )


class CompoundTests(unittest.TestCase):
    def _runtime(self, *, active_reviewer: bool = False) -> tuple[Path, tuple[Trajectory, ...], SkillBank]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = initialize_layout(Path(temporary.name))
        if active_reviewer:
            active_skill = root / "skills" / "active" / "reviewer" / "SKILL.md"
            active_skill.parent.mkdir(parents=True, exist_ok=True)
            active_skill.write_bytes(
                b"---\nname: reviewer\ndescription: Review code changes\n---\n\n# Reviewer\n\nCheck the diff.\n"
            )
        with LedgerStore.open(root) as store:
            store.ingest_jsonl(FIXTURE_PATH)
            records = tuple(Trajectory.from_mapping(item) for item in store.export_records())
        bank = SkillBank(
            root / "skills" / "active",
            root / "skills" / "candidates",
            root / "skills" / "approved",
        )
        return root, records, bank

    def test_batch_is_deterministic_and_does_not_touch_active_or_approved(self) -> None:
        root_a, records_a, bank_a = self._runtime()
        root_b, records_b, bank_b = self._runtime()
        run_a = compound_trajectories(records_a, bank_a, root_a / "skills" / "candidates", config=CompoundConfig(seed=17))
        run_b = compound_trajectories(tuple(reversed(records_b)), bank_b, root_b / "skills" / "candidates", config=CompoundConfig(seed=17))

        self.assertEqual(run_a.run_id, run_b.run_id)
        self.assertEqual(
            [(item.identity, item.action, item.candidate_id, item.candidate_digest) for item in run_a.candidates],
            [(item.identity, item.action, item.candidate_id, item.candidate_digest) for item in run_b.candidates],
        )
        self.assertEqual(
            [item.to_dict() for item in run_a.reflections],
            [item.to_dict() for item in run_b.reflections],
        )
        self.assertEqual(list((root_a / "skills" / "active").rglob("*")), [])
        self.assertEqual(list((root_a / "skills" / "approved").rglob("*")), [])
        generator_artifact = Path(run_a.artifact_paths["generator"]).read_text(encoding="utf-8")
        self.assertNotIn("sk-test-secret-123456789", generator_artifact)

    def test_conflicting_local_proposals_are_curated_to_one_candidate(self) -> None:
        root, records, bank = self._runtime()
        diagnoses = (
            _custom_diagnosis("diagnosis-a", "tool:search_files", "Prefer the indexed search path."),
            _custom_diagnosis("diagnosis-b", "tool:search_files", "Prefer the bounded search path."),
        )
        run = compound_trajectories(
            records[:0],
            bank,
            root / "skills" / "candidates",
            config=CompoundConfig(seed=4),
            diagnoses=diagnoses,
        )

        self.assertEqual(len(run.generated), 2)
        self.assertEqual(len(run.reflections), 1)
        self.assertTrue(run.reflections[0].conflict)
        self.assertEqual(len(run.curated), 1)
        self.assertEqual(len(run.candidates), 1)
        self.assertEqual(run.candidates[0].status, "proposed")
        provenance = json.loads(
            (root / "skills" / "candidates" / run.candidates[0].candidate_id / "provenance.json").read_text(encoding="utf-8")
        )
        self.assertEqual(provenance["trajectory_ids"], ["fixture-001", "fixture-002"])
        self.assertEqual(provenance["compound_provenance"]["run_id"], run.run_id)
        self.assertEqual(provenance["compound_provenance"]["group_id"], run.curated[0].group_id)
        self.assertTrue(provenance["compound_provenance"]["conflict"])
        self.assertEqual(run.curated[0].recurrence_count, 2)

    def test_ineligible_supplied_diagnosis_cannot_bypass_compound_thresholds(self) -> None:
        root, records, bank = self._runtime()
        ineligible = Diagnosis(
            id="diagnosis-one-off",
            signature="one-off",
            diagnosis_class="one-off-incident",
            target="tool:search_files",
            target_confidence=0.99,
            recurrence_count=1,
            promotable=True,
            status="eligible",
            rationale="Invalid supplied eligibility.",
            trajectory_ids=("fixture-001",),
            evidence_ids=("fixture-001",),
            event_ids=("fixture-001-e1",),
        )
        run = compound_trajectories(
            records[:0],
            bank,
            root / "skills" / "candidates",
            diagnoses=(ineligible,),
        )
        self.assertEqual(run.generated, ())

    def test_same_capability_is_revised_or_suppressed_not_duplicated(self) -> None:
        root, records, bank = self._runtime(active_reviewer=True)
        diagnoses = (_custom_diagnosis("diagnosis-review", "reviewer", "Always verify the safety boundary."),)
        first = compound_trajectories(
            records[:0], bank, root / "skills" / "candidates", config=CompoundConfig(seed=2), diagnoses=diagnoses
        )
        second = compound_trajectories(
            records[:0], bank, root / "skills" / "candidates", config=CompoundConfig(seed=2), diagnoses=diagnoses
        )

        self.assertEqual(first.candidates[0].status, "proposed")
        self.assertEqual(second.candidates[0].status, "suppressed")
        candidate_dirs = [item for item in (root / "skills" / "candidates").iterdir() if item.is_dir()]
        self.assertEqual(len(candidate_dirs), 1)
        self.assertEqual(first.candidates[0].identity, "skill|reviewer/SKILL.md")

    def test_report_contains_learning_changes_performance_and_lifecycle_events(self) -> None:
        root, records, bank = self._runtime()
        run = compound_trajectories(records, bank, root / "skills" / "candidates", config=CompoundConfig(seed=9))
        report = run.report(
            evaluations=[
                {
                    "id": "evaluation-demo",
                    "candidate_id": "candidate-demo",
                    "status": "eligible",
                    "baseline": {"task_success_rate": 0.5, "validator_score": 0.5, "cost": 1.0, "tokens": 10, "context_tokens": 5},
                    "candidate": {"task_success_rate": 0.75, "validator_score": 0.8, "cost": 1.1, "tokens": 12, "context_tokens": 6},
                    "delta": {"task_success_rate": 0.25, "validator_score": 0.3, "cost": 0.1, "tokens": 2, "context_tokens": 1},
                }
            ],
            retrieval=[{"id": "retrieval-demo", "task_id": "task-demo", "query_digest": "a" * 64, "selections": []}],
            promotions=[{"candidate_id": "candidate-demo", "commit": "abc"}],
            rollbacks=[{"candidate_id": "candidate-demo", "commit": "def"}],
        )
        self.assertIsInstance(report, CompoundReport)
        payload = report.to_dict()
        self.assertEqual(payload["trajectory_count"], 20)
        self.assertTrue(payload["learned"])
        self.assertEqual(payload["evaluations"][0]["delta"]["context_tokens"], 1)
        markdown = report.to_markdown()
        self.assertIn("What Axel Learned", markdown)
        self.assertIn("before/after", markdown.lower())
        self.assertIn("cost delta", markdown)
        json_path = root / "reports" / "compound.json"
        markdown_path = root / "reports" / "compound.md"
        write_compound_report(report, json_path, markdown_path)
        self.assertTrue(json_path.is_file())
        self.assertTrue(markdown_path.is_file())

        with self.assertRaises(CompoundError):
            write_compound_report(report, root / "same.json", root / "same.json")
        with self.assertRaises(CompoundError):
            write_compound_report(report, root / "skills" / "approved" / "report.json", root / "reports" / "safe.md")

    def test_full_fixture_flow_rejects_harmful_candidate_promotes_rolls_back_and_retrieves(self) -> None:
        from tests.test_promotion import PromotionTests

        helper = PromotionTests()
        root, records, bank = self._runtime(active_reviewer=True)
        helper._git_init(root)
        diagnosis = _custom_diagnosis("diagnosis-review", "reviewer", "Repeated reviews omitted the safety boundary.")
        run = compound_trajectories(
            records,
            bank,
            root / "skills" / "candidates",
            config=CompoundConfig(seed=21),
            diagnoses=(diagnosis,),
        )
        outcome = run.candidates[0]
        candidate_root = root / "skills" / "candidates"
        candidate_path = candidate_root / str(outcome.candidate_id)
        provenance_path = candidate_path / "provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        transition_candidate(
            candidate_root,
            candidate_path.name,
            "tested",
            expected_status=provenance["status"],
            expected_candidate_digest=provenance["candidate_digest"],
            expected_parent_digest=provenance.get("parent_digest"),
            expected_diff_digest=provenance["diff_digest"],
            expected_provenance_digest=provenance["provenance_digest"],
            configured_candidate_root=candidate_root,
            active_root=root / "skills" / "active",
            approved_root=root / "skills" / "approved",
        )
        evaluation = helper._evaluation(root, candidate_path, candidate_path.name, provenance["candidate_digest"])
        helper._git(root, "add", "--all")
        helper._git(root, "commit", "-m", "compound batch")
        approval = helper._approve(candidate_path, candidate_root, provenance["candidate_digest"], evaluation)
        promoted = promote_candidate(
            candidate_root,
            candidate_path.name,
            evaluation,
            root / "skills" / "approved",
            root,
            expected_approval_digest=approval.approval_digest,
            expected_candidate_digest=provenance["candidate_digest"],
            active_root=root / "skills" / "active",
        )
        retrieved = retrieve_approved_assets(root / "skills" / "approved", query="safety boundary", threshold=0.1)
        self.assertTrue(retrieved.context)
        self.assertEqual(retrieved.context[0].selection.candidate_id, candidate_path.name)

        harmful = {
            "id": "harmful",
            "class": "skill",
            "target": "reviewer",
            "confidence": 0.9,
            "target_confidence": 0.9,
            "status": "eligible",
            "promotable": True,
            "recurrence_count": 2,
            "trajectory_ids": ["fixture-001", "fixture-002"],
            "evidence_ids": ["fixture-001", "fixture-002"],
            "rationale": "Harmful candidate fixture.",
            "proposed_content": "---\nname: reviewer\ndescription: Review code changes\n---\n\n# Reviewer\n\nSkip the safety boundary.\n",
            "evaluation_rules": [{"id": "harmful-fixture", "type": "fixture"}],
        }
        harmful_result = propose_skill_candidate(harmful, bank, candidate_root)
        harmful_path = candidate_root / str(harmful_result.candidate_id)
        harmful_provenance = json.loads((harmful_path / "provenance.json").read_text(encoding="utf-8"))
        transition_candidate(
            candidate_root,
            harmful_path.name,
            "tested",
            expected_status=harmful_provenance["status"],
            expected_candidate_digest=harmful_provenance["candidate_digest"],
            expected_parent_digest=harmful_provenance.get("parent_digest"),
            expected_diff_digest=harmful_provenance["diff_digest"],
            expected_provenance_digest=harmful_provenance["provenance_digest"],
            configured_candidate_root=candidate_root,
            active_root=root / "skills" / "active",
            approved_root=root / "skills" / "approved",
        )
        harmful_evaluation = helper._evaluation(root, harmful_path, harmful_path.name, harmful_provenance["candidate_digest"], status="rejected")
        with self.assertRaises(PromotionError):
            helper._approve(harmful_path, candidate_root, harmful_provenance["candidate_digest"], harmful_evaluation)

        rolled_back = rollback_candidate(
            candidate_root,
            candidate_path.name,
            root / "skills" / "approved",
            root,
            operator="operator@example.invalid",
            reason="compound demonstration rollback",
            active_root=root / "skills" / "active",
        )
        after_rollback = retrieve_approved_assets(root / "skills" / "approved", query="safety boundary", threshold=0.1)
        self.assertEqual(after_rollback.context, ())
        report = run.report(
            evaluations=[json.loads(evaluation.read_text(encoding="utf-8"))],
            retrieval=[retrieved.record, after_rollback.record],
            promotions=[promoted],
            rollbacks=[rolled_back],
            rejections=[{"candidate_id": harmful_path.name, "reason": "regressive evaluation rejected"}],
        )
        self.assertEqual(len(report.promotions), 1)
        self.assertEqual(len(report.rollbacks), 1)
        self.assertEqual(len(report.rejections), 1)
        self.assertEqual(report.retrieval[0]["selection_count"], 1)


if __name__ == "__main__":
    unittest.main()

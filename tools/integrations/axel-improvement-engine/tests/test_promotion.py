"""Tests for explicit approval, Git promotion, and rollback."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from axel_improve.candidates import propose_skill_candidate, transition_candidate
from axel_improve.candidates import provenance_digest
from axel_improve.promotion import PromotionError, approve_candidate, promote_candidate, rollback_candidate
from axel_improve.skills import SkillBank


ROOT = Path(__file__).resolve().parents[1]
DIAGNOSIS_PATH = ROOT / "tests" / "fixtures" / "skill_diagnosis.json"


class PromotionTests(unittest.TestCase):
    def _git(self, root: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def _fixture(self) -> dict:
        return json.loads(DIAGNOSIS_PATH.read_text(encoding="utf-8"))

    def _layout(self, root: Path) -> tuple[SkillBank, Path, Path, Path]:
        active = root / "skills" / "active"
        candidates = root / "skills" / "candidates"
        approved = root / "skills" / "approved"
        active_skill = active / "reviewer" / "SKILL.md"
        active_skill.parent.mkdir(parents=True, exist_ok=True)
        active_skill.write_bytes(
            b"---\nname: reviewer\ndescription: Review code changes\n---\n\n# Reviewer\n\nCheck the diff.\n"
        )
        bank = SkillBank(active, candidates, approved)
        return bank, candidates, active, approved

    def _git_init(self, root: Path) -> None:
        self._git(root, "init", "-b", "main")
        self._git(root, "config", "user.email", "axel-tests@example.invalid")
        self._git(root, "config", "user.name", "Axel Tests")

    def _prepare_candidate(self, root: Path, diagnosis: dict | None = None) -> tuple[Path, Path, str, Path, Path, Path]:
        bank, candidate_root, active_root, approved_root = self._layout(root)
        diagnosis = deepcopy(diagnosis or self._fixture())
        result = propose_skill_candidate(diagnosis, bank, candidate_root)
        self.assertEqual(result.action, "created")
        assert result.candidate_id is not None
        candidate_path = candidate_root / result.candidate_id
        provenance_path = candidate_path / "provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        transition_candidate(
            candidate_root,
            result.candidate_id,
            "tested",
            expected_status=provenance["status"],
            expected_candidate_digest=provenance["candidate_digest"],
            expected_parent_digest=provenance.get("parent_digest"),
            expected_diff_digest=provenance["diff_digest"],
            expected_provenance_digest=provenance["provenance_digest"],
            configured_candidate_root=candidate_root,
            active_root=active_root,
            approved_root=approved_root,
        )
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        candidate_digest = provenance["candidate_digest"]
        return candidate_path, candidate_root, candidate_digest, active_root, approved_root, root

    def _evaluation(self, root: Path, candidate_path: Path, candidate_id: str, candidate_digest: str, status: str = "eligible") -> Path:
        provenance = json.loads((candidate_path / "provenance.json").read_text(encoding="utf-8"))
        diff_digest = provenance["diff_digest"]
        candidate_provenance_digest = provenance_digest(provenance)
        target = provenance.get("target")
        asset_key = f"target:{target}" if target else f"name:{provenance['new_skill_name']}"
        payload = {
            "schema_version": 1,
            "id": f"evaluation-{candidate_id}",
            "candidate_id": candidate_id,
            "candidate_digest": candidate_digest,
            "candidate_asset_reference": str(candidate_path.resolve()),
            "candidate_diff_digest": diff_digest,
            "candidate_provenance_digest": candidate_provenance_digest,
            "candidate_target": target,
            "candidate_parent_digest": provenance.get("parent_digest"),
            "candidate_asset_key": asset_key,
            "suite_id": "suite-promotion-test",
            "seed": 11,
            "runner_version": "deterministic-fixture-v1",
            "provider_version": "none",
            "status": status,
            "config": {
                "min_evidence": 1,
                "require_held_out": False,
                "min_success_delta": 0.0,
                "non_inferiority_margin": 0.0,
                "max_held_out_regressions": 0,
                "max_critical_regressions": 0,
                "max_regressions": 0,
                "max_token_overhead": None,
                "max_context_overhead": None,
                "max_cost_overhead": None,
                "max_candidate_cost": None,
            },
            "baseline": {
                "cases": 1,
                "task_successes": 1,
                "task_success_rate": 1.0,
                "validator_score": 1.0,
                "validator_scored_cases": 1,
                "critical_failures": 0,
                "incomplete": 0,
                "run_failures": 0,
                "latency_ms": 1,
                "tokens": 1,
                "context_tokens": 0,
                "cost": None,
                "missing_metrics": ["cost"],
            },
            "candidate": {
                "cases": 1,
                "task_successes": 1,
                "task_success_rate": 1.0,
                "validator_score": 1.0,
                "validator_scored_cases": 1,
                "critical_failures": 0,
                "incomplete": 0,
                "run_failures": 0,
                "latency_ms": 1,
                "tokens": 1,
                "context_tokens": 0,
                "cost": None,
                "missing_metrics": ["cost"],
            },
            "delta": {
                "task_success_rate": 0.0,
                "validator_score": 0.0,
                "critical_failures": 0,
                "incomplete": 0,
                "run_failures": 0,
                "latency_ms": 0,
                "tokens": 0,
                "context_tokens": 0,
                "cost": None,
            },
            "comparisons": [{
                "case_id": "case-1",
                "split": "held-out",
                "baseline_status": "passed",
                "candidate_status": "passed",
                "baseline_outcome_status": "success",
                "candidate_outcome_status": "success",
                "regression": False,
                "critical_regression": False,
                "baseline_task_success": True,
                "candidate_task_success": True,
                "baseline_budget": {"duration_ms": 1, "tokens_used": 1, "context_tokens": 0},
                "candidate_budget": {"duration_ms": 1, "tokens_used": 1, "context_tokens": 0},
                "baseline_score": 1.0,
                "candidate_score": 1.0,
                "score_delta": 0.0,
                "baseline_validators": [{"name": "replay-status", "passed": True, "critical": True, "score": 1.0}],
                "candidate_validators": [{"name": "replay-status", "passed": True, "critical": True, "score": 1.0}],
            }],
            "gates": [
                {"name": name, "passed": status == "eligible", "hard": True}
                for name in (
                    "same_cases",
                    "minimum_evidence",
                    "complete_runs",
                    "metric_completeness",
                    "deterministic_validators",
                    "held_out_regressions",
                    "critical_regressions",
                    "regressions",
                    "improvement_or_non_inferiority",
                    "cost_and_token_budget",
                )
            ],
        }
        path = root / "evaluations" / f"{candidate_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        return path

    def _approve(self, candidate_path: Path, candidate_root: Path, candidate_digest: str, evaluation: Path):
        candidate_id = candidate_path.name
        result = approve_candidate(
            candidate_root,
            candidate_id,
            evaluation,
            candidate_digest=candidate_digest,
            operator="operator@example.invalid",
            reason="verified by promotion test",
            active_root=candidate_root.parent / "active",
            approved_root=candidate_root.parent / "approved",
        )
        self.assertEqual(result.status, "awaiting_approval")
        return result

    def test_approval_and_promotion_require_exact_eligible_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git_init(root)
            candidate_path, candidate_root, digest, active_root, approved_root, _ = self._prepare_candidate(root)
            evaluation = self._evaluation(root, candidate_path, candidate_path.name, digest)
            self._git(root, "add", "--all")
            self._git(root, "commit", "-m", "initial candidate")

            with self.assertRaises(PromotionError):
                approve_candidate(
                    candidate_root,
                    candidate_path.name,
                    evaluation,
                    candidate_digest="0" * 64,
                    operator="operator@example.invalid",
                    reason="wrong digest",
                    active_root=active_root,
                    approved_root=approved_root,
                )

            approval = self._approve(candidate_path, candidate_root, digest, evaluation)
            self.assertFalse(approved_root.exists() and any(approved_root.iterdir()))
            promoted = promote_candidate(
                candidate_root,
                candidate_path.name,
                evaluation,
                approved_root,
                root,
                expected_approval_digest=approval.approval_digest,
                expected_candidate_digest=digest,
                active_root=active_root,
            )

            self.assertTrue((approved_root / candidate_path.name / "SKILL.md").is_file())
            self.assertEqual((approved_root / candidate_path.name / "SKILL.md").read_bytes(), (candidate_path / "SKILL.md").read_bytes())
            self.assertEqual(promoted.commit, self._git(root, "rev-parse", "HEAD"))
            self.assertIn(candidate_path.name, self._git(root, "log", "-1", "--format=%s"))
            manifest = json.loads((approved_root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["active"]["target:reviewer/SKILL.md"]["candidate_id"], candidate_path.name)
            candidate_provenance = json.loads((candidate_path / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(candidate_provenance["status"], "approved")
            self.assertEqual(
                (active_root / "reviewer" / "SKILL.md").read_bytes(),
                b"---\nname: reviewer\ndescription: Review code changes\n---\n\n# Reviewer\n\nCheck the diff.\n",
            )

    def test_regressive_candidate_cannot_be_approved_and_tampering_blocks_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git_init(root)
            candidate_path, candidate_root, digest, active_root, approved_root, _ = self._prepare_candidate(root)
            evaluation = self._evaluation(root, candidate_path, candidate_path.name, digest, status="rejected")
            with self.assertRaises(PromotionError):
                approve_candidate(
                    candidate_root,
                    candidate_path.name,
                    evaluation,
                    candidate_digest=digest,
                    operator="operator@example.invalid",
                    reason="harmful candidate",
                    active_root=active_root,
                    approved_root=approved_root,
                )
            self.assertFalse(approved_root.exists() and any(approved_root.iterdir()))

            evaluation = self._evaluation(root, candidate_path, candidate_path.name, digest)
            approval = self._approve(candidate_path, candidate_root, digest, evaluation)
            (candidate_path / "SKILL.md").write_bytes(b"tampered after approval\n")
            with self.assertRaises(PromotionError):
                promote_candidate(candidate_root, candidate_path.name, evaluation, approved_root, root, expected_approval_digest=approval.approval_digest, active_root=active_root)
            self.assertFalse(approved_root.exists() and any(approved_root.iterdir()))

    def test_stale_parent_blocks_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git_init(root)
            candidate_path, candidate_root, digest, active_root, approved_root, _ = self._prepare_candidate(root)
            evaluation = self._evaluation(root, candidate_path, candidate_path.name, digest)
            active_skill = active_root / "reviewer" / "SKILL.md"
            active_skill.write_bytes(active_skill.read_bytes() + b"changed before approval\n")
            with self.assertRaisesRegex(PromotionError, "stale"):
                approve_candidate(
                    candidate_root,
                    candidate_path.name,
                    evaluation,
                    candidate_digest=digest,
                    operator="operator@example.invalid",
                    reason="stale test",
                    active_root=active_root,
                    approved_root=approved_root,
                )
            self.assertFalse((candidate_path / "approval.json").exists())

    def test_semantically_inconsistent_evaluation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git_init(root)
            candidate_path, candidate_root, digest, active_root, approved_root, _ = self._prepare_candidate(root)
            evaluation = self._evaluation(root, candidate_path, candidate_path.name, digest)
            payload = json.loads(evaluation.read_text(encoding="utf-8"))
            payload["candidate"]["task_successes"] = 0
            evaluation.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(PromotionError, "metric task_successes"):
                approve_candidate(
                    candidate_root,
                    candidate_path.name,
                    evaluation,
                    candidate_digest=digest,
                    operator="operator@example.invalid",
                    reason="semantic validation test",
                    active_root=active_root,
                    approved_root=approved_root,
                )

    def test_approval_binds_evaluated_candidate_provenance_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git_init(root)
            candidate_path, candidate_root, digest, active_root, approved_root, _ = self._prepare_candidate(root)
            evaluation = self._evaluation(root, candidate_path, candidate_path.name, digest)
            provenance_path = candidate_path / "provenance.json"
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            provenance["new_skill_name"] = "reclassified-candidate"
            provenance["provenance_digest"] = provenance_digest(provenance)
            provenance_path.write_text(json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(PromotionError, "exact candidate"):
                approve_candidate(
                    candidate_root,
                    candidate_path.name,
                    evaluation,
                    candidate_digest=digest,
                    operator="operator@example.invalid",
                    reason="snapshot validation test",
                    active_root=active_root,
                    approved_root=approved_root,
                )

    def test_promotion_restores_files_when_index_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git_init(root)
            candidate_path, candidate_root, digest, active_root, approved_root, _ = self._prepare_candidate(root)
            evaluation = self._evaluation(root, candidate_path, candidate_path.name, digest)
            self._git(root, "add", "--all")
            self._git(root, "commit", "-m", "initial candidate")
            approval = self._approve(candidate_path, candidate_root, digest, evaluation)

            with (
                mock.patch("axel_improve.promotion._commit_paths", side_effect=PromotionError("commit failed")),
                mock.patch("axel_improve.promotion._reset_paths", side_effect=PromotionError("reset failed")),
            ):
                with self.assertRaisesRegex(PromotionError, "commit failed"):
                    promote_candidate(
                        candidate_root,
                        candidate_path.name,
                        evaluation,
                        approved_root,
                        root,
                        expected_approval_digest=approval.approval_digest,
                        active_root=active_root,
                    )
            self.assertFalse((approved_root / candidate_path.name).exists())
            self.assertFalse((approved_root / "manifest.json").exists())
            self.assertEqual(self._git(root, "diff", "--cached", "--name-only"), "")

    def test_rollback_restores_previous_approved_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git_init(root)
            first_path, candidate_root, first_digest, active_root, approved_root, _ = self._prepare_candidate(root)
            first_eval = self._evaluation(root, first_path, first_path.name, first_digest)
            self._git(root, "add", "--all")
            self._git(root, "commit", "-m", "initial candidate")
            first_approval = self._approve(first_path, candidate_root, first_digest, first_eval)
            promote_candidate(candidate_root, first_path.name, first_eval, approved_root, root, expected_approval_digest=first_approval.approval_digest, active_root=active_root)

            second_diagnosis = self._fixture()
            second_diagnosis["id"] = "diagnosis-005"
            second_diagnosis["proposed_content"] = second_diagnosis["proposed_content"].replace("Always check", "First check")
            second_path, _, second_digest, _, _, _ = self._prepare_candidate(root, second_diagnosis)
            second_eval = self._evaluation(root, second_path, second_path.name, second_digest)
            second_approval = self._approve(second_path, candidate_root, second_digest, second_eval)
            promote_candidate(candidate_root, second_path.name, second_eval, approved_root, root, expected_approval_digest=second_approval.approval_digest, active_root=active_root)

            rollback = rollback_candidate(
                candidate_root,
                second_path.name,
                approved_root,
                root,
                operator="operator@example.invalid",
                reason="rollback test",
                active_root=active_root,
            )
            self.assertEqual(rollback.restored_candidate_id, first_path.name)
            self.assertEqual(rollback.restored_digest, first_digest)
            manifest = json.loads((approved_root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["active"]["target:reviewer/SKILL.md"]["candidate_id"], first_path.name)
            second_provenance = json.loads((second_path / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(second_provenance["status"], "rolled_back")
            self.assertIn("rollback", self._git(root, "log", "-2", "--format=%s"))

    def test_missing_git_and_dirty_approved_target_leave_assets_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git_init(root)
            candidate_path, candidate_root, digest, active_root, approved_root, _ = self._prepare_candidate(root)
            evaluation = self._evaluation(root, candidate_path, candidate_path.name, digest)
            approval = self._approve(candidate_path, candidate_root, digest, evaluation)
            with tempfile.TemporaryDirectory() as no_git:
                with self.assertRaises(PromotionError):
                    promote_candidate(candidate_root, candidate_path.name, evaluation, approved_root, Path(no_git), expected_approval_digest=approval.approval_digest, active_root=active_root)
            self.assertFalse(approved_root.exists() and any(approved_root.iterdir()))

    def test_dirty_approved_manifest_blocks_promotion_without_overwriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git_init(root)
            first_path, candidate_root, first_digest, active_root, approved_root, _ = self._prepare_candidate(root)
            first_eval = self._evaluation(root, first_path, first_path.name, first_digest)
            self._git(root, "add", "--all")
            self._git(root, "commit", "-m", "initial candidate")
            first_approval = self._approve(first_path, candidate_root, first_digest, first_eval)
            promote_candidate(candidate_root, first_path.name, first_eval, approved_root, root, expected_approval_digest=first_approval.approval_digest, active_root=active_root)
            manifest_path = approved_root / "manifest.json"
            original_dirty = manifest_path.read_bytes() + b" "
            manifest_path.write_bytes(original_dirty)

            second_diagnosis = self._fixture()
            second_diagnosis["id"] = "diagnosis-005"
            second_diagnosis["proposed_content"] = second_diagnosis["proposed_content"].replace("Always check", "First check")
            second_path, _, second_digest, _, _, _ = self._prepare_candidate(root, second_diagnosis)
            second_eval = self._evaluation(root, second_path, second_path.name, second_digest)
            second_approval = self._approve(second_path, candidate_root, second_digest, second_eval)
            with self.assertRaisesRegex(PromotionError, "dirty"):
                promote_candidate(candidate_root, second_path.name, second_eval, approved_root, root, expected_approval_digest=second_approval.approval_digest, active_root=active_root)
            self.assertEqual(manifest_path.read_bytes(), original_dirty)
            self.assertFalse((approved_root / second_path.name).exists())


if __name__ == "__main__":
    unittest.main()

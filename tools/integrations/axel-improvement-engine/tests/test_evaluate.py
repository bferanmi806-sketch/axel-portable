"""Tests for candidate evaluation, regression gates, and champion recording."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from axel_improve.evaluate import (
    ChampionRegistry,
    DeterministicComparisonRunner,
    EvaluationConfig,
    EvaluationError,
    evaluate_candidate,
    write_evaluation,
)
from axel_improve.candidates import provenance_digest
from axel_improve.redaction import Redactor
from axel_improve.replay import build_replay_suite, run_replay_suite


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "reconstructed.jsonl"


def fixture_records() -> list[dict]:
    return [json.loads(line) for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines()]


def baseline_results(suite) -> dict[str, dict]:
    return {result.case_id: result.to_dict() for result in run_replay_suite(suite)}


def refresh_result_digest(case, result: dict) -> None:
    payload = {
        "case_id": case.id,
        "case_digest": case.digest(),
        "status": result["status"],
        "reason": result["reason"],
        "evidence": result["evidence"],
        "budget": result["budget"],
    }
    result["result_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def successful_candidate_result(case, value: dict) -> dict:
    result = deepcopy(value)
    result["status"] = "passed"
    result["reason"] = "candidate fixture improved the task"
    result["evidence"]["outcome_status"] = "success"
    result["evidence"]["outcome_summary"] = "Candidate result passed"
    result["evidence"]["score"] = 1.0
    for validator in result["evidence"]["validators"]:
        validator["passed"] = True
        validator["score"] = 1.0
    refresh_result_digest(case, result)
    return result


class EvaluateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = build_replay_suite(fixture_records(), seed=17, redactor=Redactor())
        self.baseline = baseline_results(self.suite)
        self._candidate_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._candidate_temporary.cleanup)
        self.candidate_root = Path(self._candidate_temporary.name) / "candidates"
        self.candidate_root.mkdir()
        (self.candidate_root / ".axel-candidate-root").write_bytes(
            b"Axel Improvement Engine candidate root\n"
        )
        self._assets: dict[str, tuple[Path, str, dict[str, object]]] = {}

    def candidate_context(
        self, candidate_id: str, config: EvaluationConfig | None = None
    ) -> tuple[Path, str, dict[str, object]]:
        cached = self._assets.get(candidate_id)
        if cached is not None:
            return cached
        asset = self.candidate_root / candidate_id
        asset.mkdir()
        content = f"---\nname: {candidate_id}\n---\n\n# Candidate\n"
        (asset / "SKILL.md").write_bytes(content.encode("utf-8"))
        digest = hashlib.sha256((asset / "SKILL.md").read_bytes()).hexdigest()
        diff = b"candidate diff\n"
        (asset / "change.diff").write_bytes(diff)
        provenance = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "candidate_digest": digest,
            "diff_digest": hashlib.sha256(diff).hexdigest(),
            "target": None,
            "parent_digest": None,
            "new_skill_name": candidate_id,
            "status": "proposed",
        }
        provenance["provenance_digest"] = provenance_digest(provenance)
        (asset / "provenance.json").write_bytes(json.dumps(provenance).encode("utf-8"))
        active = config or EvaluationConfig()
        context = (
            asset,
            digest,
            {
                "candidate_id": candidate_id,
                "candidate_digest": digest,
                "suite_id": self.suite.id,
                "seed": self.suite.seed,
                "runner_version": active.runner_version,
                "config_digest": hashlib.sha256(
                    json.dumps(active.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "asset_reference": str(asset.resolve()),
            },
        )
        self._assets[candidate_id] = context
        return context

    def evaluation_kwargs(self, candidate_id: str, config: EvaluationConfig | None = None) -> dict[str, object]:
        asset, digest, manifest = self.candidate_context(candidate_id, config)
        return {
            "candidate_digest": digest,
            "candidate_manifest": manifest,
            "candidate_asset": asset,
            "candidate_root": self.candidate_root,
        }

    def test_better_candidate_becomes_eligible_without_writing_approved_assets(self) -> None:
        candidate = deepcopy(self.baseline)
        target = next(
            case for case in self.suite.cases if case.expected_evidence["outcome_status"] != "success"
        )
        candidate[target.id] = successful_candidate_result(target, candidate[target.id])

        with tempfile.TemporaryDirectory() as temporary:
            evaluation = evaluate_candidate(
                self.suite,
                candidate_id="candidate-better",
                baseline_digest="baseline-digest",
                candidate_results=candidate,
                **self.evaluation_kwargs("candidate-better"),
            )
            artifact = write_evaluation(Path(temporary) / "evaluation.json", evaluation)

            self.assertEqual(evaluation.status, "eligible")
            self.assertTrue(artifact.is_file())
            self.assertFalse((Path(temporary) / "approved").exists())

            approved = Path(temporary) / "skills" / "approved"
            with self.assertRaises(EvaluationError):
                write_evaluation(approved / "evaluation.json", evaluation)

    def test_harmful_held_out_candidate_is_rejected(self) -> None:
        candidate = deepcopy(self.baseline)
        target = next(case for case in self.suite.held_out_cases if case.expected_evidence["outcome_status"] == "success")
        candidate[target.id]["status"] = "failed"
        candidate[target.id]["reason"] = "harmful fixture regression"
        candidate[target.id]["evidence"]["score"] = 0.0
        candidate[target.id]["evidence"]["outcome_status"] = "failure"
        refresh_result_digest(target, candidate[target.id])

        evaluation = evaluate_candidate(
            self.suite,
            candidate_id="candidate-harmful",
            baseline_digest="baseline-digest",
            candidate_results=candidate,
            **self.evaluation_kwargs("candidate-harmful"),
        )

        self.assertEqual(evaluation.status, "rejected")
        self.assertTrue(any(item.name == "held_out_regressions" and not item.passed for item in evaluation.gates))
        self.assertTrue(any(item.name == "deterministic_validators" and not item.passed for item in evaluation.gates))

    def test_regression_gate_rejects_higher_average_with_one_worse_case(self) -> None:
        candidate = deepcopy(self.baseline)
        highest = max(self.suite.cases, key=lambda case: case.expected_evidence["score"] or 0.0)
        for case_id, result in candidate.items():
            result["evidence"]["score"] = 1.0
            for validator in result["evidence"]["validators"]:
                validator["score"] = 1.0
            refresh_result_digest(next(case for case in self.suite.cases if case.id == case_id), result)
        candidate[highest.id]["evidence"]["score"] = 0.0
        for validator in candidate[highest.id]["evidence"]["validators"]:
            validator["score"] = 0.0
        refresh_result_digest(highest, candidate[highest.id])

        class EvidenceScoreValidator:
            name = "fixture-score"

            def validate(self, case, result):
                score = result.evidence.get("score")
                return {
                    "name": self.name,
                    "passed": isinstance(score, (int, float)) and score >= 0.5,
                    "score": score,
                    "critical": False,
                }

        evaluation = evaluate_candidate(
            self.suite,
            candidate_id="candidate-regressive-average",
            baseline_digest="baseline-digest",
            candidate_results=candidate,
            **self.evaluation_kwargs("candidate-regressive-average"),
            validators=(EvidenceScoreValidator(),),
        )

        self.assertGreater(evaluation.candidate.validator_score or 0.0, evaluation.baseline.validator_score or 0.0)
        self.assertEqual(evaluation.status, "rejected")
        self.assertTrue(any(item.name == "regressions" and not item.passed for item in evaluation.gates))

    def test_missing_cost_metric_fails_a_configured_cost_gate(self) -> None:
        candidate = deepcopy(self.baseline)
        first_case = self.suite.cases[0]
        candidate[first_case.id]["budget"]["cost"] = 0.0
        refresh_result_digest(first_case, candidate[first_case.id])
        evaluation = evaluate_candidate(
            self.suite,
            candidate_id="candidate-cost",
            baseline_digest="baseline-digest",
            candidate_results=candidate,
            config=EvaluationConfig(max_cost_overhead=0.0),
            **self.evaluation_kwargs("candidate-cost", EvaluationConfig(max_cost_overhead=0.0)),
        )

        self.assertEqual(evaluation.status, "rejected")
        self.assertTrue(any(item.name == "cost_and_token_budget" and not item.passed for item in evaluation.gates))
        self.assertTrue(any(item.name == "metric_completeness" and not item.passed for item in evaluation.gates))

    def test_absolute_candidate_cost_gate_does_not_require_baseline_cost(self) -> None:
        candidate = deepcopy(self.baseline)
        for case in self.suite.cases:
            candidate[case.id]["budget"]["cost"] = 0.0
            refresh_result_digest(case, candidate[case.id])
        config = EvaluationConfig(max_candidate_cost=0.0)
        evaluation = evaluate_candidate(
            self.suite,
            candidate_id="candidate-absolute-cost",
            baseline_digest="baseline-digest",
            candidate_results=candidate,
            config=config,
            **self.evaluation_kwargs("candidate-absolute-cost", config),
        )

        self.assertEqual(evaluation.status, "eligible")
        self.assertTrue(all(item.passed for item in evaluation.gates if item.name == "metric_completeness"))

    def test_missing_candidate_results_are_incomplete_and_cannot_pass(self) -> None:
        evaluation = evaluate_candidate(
            self.suite,
            candidate_id="candidate-missing",
            baseline_digest="baseline-digest",
            **self.evaluation_kwargs("candidate-missing"),
        )

        self.assertEqual(evaluation.status, "rejected")
        self.assertGreater(evaluation.candidate.incomplete, 0)

    def test_candidate_evidence_and_digest_are_required(self) -> None:
        missing_evidence = deepcopy(self.baseline)
        missing_evidence[next(iter(missing_evidence))].pop("evidence")
        with self.assertRaises(EvaluationError):
            evaluate_candidate(
                self.suite,
                candidate_id="candidate-missing-evidence",
                baseline_digest="baseline-digest",
                candidate_results=missing_evidence,
                **self.evaluation_kwargs("candidate-missing-evidence"),
            )

        missing_digest = deepcopy(self.baseline)
        missing_digest[next(iter(missing_digest))].pop("result_digest")
        with self.assertRaises(EvaluationError):
            evaluate_candidate(
                self.suite,
                candidate_id="candidate-missing-digest",
                baseline_digest="baseline-digest",
                candidate_results=missing_digest,
                **self.evaluation_kwargs("candidate-missing-digest"),
            )

    def test_candidate_asset_digest_and_reference_are_verified(self) -> None:
        asset, _, _ = self.candidate_context("candidate-tampered")
        (asset / "SKILL.md").write_bytes(b"tampered candidate\n")
        with self.assertRaises(EvaluationError):
            evaluate_candidate(
                self.suite,
                candidate_id="candidate-tampered",
                baseline_digest="baseline-digest",
                candidate_results=self.baseline,
                **self.evaluation_kwargs("candidate-tampered"),
            )

    def test_context_budget_gate_fails_closed_when_metric_is_missing(self) -> None:
        config = EvaluationConfig(max_context_overhead=0)
        evaluation = evaluate_candidate(
            self.suite,
            candidate_id="candidate-context",
            baseline_digest="baseline-digest",
            candidate_results=self.baseline,
            config=config,
            **self.evaluation_kwargs("candidate-context", config),
        )

        self.assertEqual(evaluation.status, "rejected")
        self.assertTrue(any(item.name == "metric_completeness" and not item.passed for item in evaluation.gates))

    def test_evaluation_checkpoint_resumes_after_interruption(self) -> None:
        candidate_id = "candidate-checkpoint"
        candidate_kwargs = self.evaluation_kwargs(candidate_id)
        checkpoint = Path(self._candidate_temporary.name) / "evaluation.checkpoint.json"
        delegate = DeterministicComparisonRunner(self.baseline)
        suite = self.suite

        class InterruptingRunner:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def run(self, case, variant):
                self.calls.append((case.id, variant))
                if case.id == suite.cases[1].id and variant == "candidate":
                    raise KeyboardInterrupt()
                return delegate.run(case, variant)

        interrupted = InterruptingRunner()
        with self.assertRaises(KeyboardInterrupt):
            evaluate_candidate(
                self.suite,
                candidate_id=candidate_id,
                baseline_digest="baseline-digest",
                candidate_results=self.baseline,
                checkpoint_path=checkpoint,
                runner=interrupted,
                **candidate_kwargs,
            )

        checkpoint_payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(len(checkpoint_payload["completed"]), 1)

        class CountingRunner:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def run(self, case, variant):
                self.calls.append((case.id, variant))
                return delegate.run(case, variant)

        resumed = CountingRunner()
        evaluation = evaluate_candidate(
            self.suite,
            candidate_id=candidate_id,
            baseline_digest="baseline-digest",
            candidate_results=self.baseline,
            checkpoint_path=checkpoint,
            runner=resumed,
            **candidate_kwargs,
        )

        self.assertEqual(evaluation.status, "eligible")
        self.assertIn((self.suite.cases[0].id, "baseline"), resumed.calls)
        self.assertIn((self.suite.cases[0].id, "candidate"), resumed.calls)
        self.assertEqual(len(json.loads(checkpoint.read_text(encoding="utf-8"))["completed"]), len(self.suite.cases))

    def test_invalid_validator_and_judge_output_fail_closed(self) -> None:
        class InvalidValidator:
            name = "invalid"

            def validate(self, case, result):
                return {"name": "invalid", "passed": "yes"}

        class InvalidJudge:
            def judge(self, case, baseline, candidate):
                return {"score": "not-a-score", "rationale": "bad"}

        with self.assertRaises(EvaluationError):
            evaluate_candidate(
                self.suite,
                candidate_id="candidate-invalid-validator",
                baseline_digest="baseline-digest",
                candidate_results=self.baseline,
                **self.evaluation_kwargs("candidate-invalid-validator"),
                validators=(InvalidValidator(),),
            )
        with self.assertRaises(EvaluationError):
            evaluate_candidate(
                self.suite,
                candidate_id="candidate-invalid-judge",
                baseline_digest="baseline-digest",
                candidate_results=self.baseline,
                **self.evaluation_kwargs("candidate-invalid-judge"),
                model_judge=InvalidJudge(),
            )

    def test_eligible_evaluation_can_update_champion_registry_only(self) -> None:
        candidate = deepcopy(self.baseline)
        target = next(case for case in self.suite.cases if case.expected_evidence["outcome_status"] != "success")
        candidate[target.id] = successful_candidate_result(target, candidate[target.id])
        evaluation = evaluate_candidate(
            self.suite,
            candidate_id="candidate-champion",
            baseline_digest="baseline-digest",
            candidate_results=candidate,
            **self.evaluation_kwargs("candidate-champion"),
        )
        self.assertEqual(evaluation.status, "eligible")

        with tempfile.TemporaryDirectory() as temporary:
            record = ChampionRegistry(Path(temporary) / "champion.json").record(evaluation, target="reviewer")
            self.assertEqual(record["candidate_id"], "candidate-champion")
            self.assertFalse((Path(temporary) / "approved").exists())

            with self.assertRaises(EvaluationError):
                ChampionRegistry(
                    Path(temporary) / "skills" / "approved" / "champion.json",
                    protected_roots=(Path(temporary) / "skills" / "approved",),
                ).record(evaluation)


if __name__ == "__main__":
    unittest.main()

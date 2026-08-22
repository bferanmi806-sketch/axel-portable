"""Baseline-versus-candidate evaluation and promotion-safety gates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from .candidates import MAX_DIFF_BYTES, provenance_digest
from .replay import (
    DeterministicFixtureRunner,
    ReplayCase,
    ReplayRunResult,
    ReplaySuite,
)
from .skills import SkillAssetError, _is_link, _reject_link_ancestors, read_bounded
from .store import atomic_write_text


EVALUATION_SCHEMA_VERSION = 1
MAX_EVALUATION_BYTES = 16 * 1024 * 1024
MAX_CANDIDATE_BODY_BYTES = 131_072
MAX_CANDIDATE_PROVENANCE_BYTES = 65_536
MAX_CASES = 10_000
VALID_RUN_STATUSES = frozenset({"passed", "failed", "incomplete"})
VALID_EVALUATION_STATUSES = frozenset({"eligible", "rejected", "incubating"})
CANDIDATE_ROOT_MARKER = ".axel-candidate-root"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class EvaluationError(ValueError):
    """Raised when evaluation input or validator output fails closed."""


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise EvaluationError("evaluation data must be JSON-compatible") from exc


def _text(value: Any, field: str, *, required: bool = True, limit: int = 8_192) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise EvaluationError(f"{field} must be a non-empty string")
    text = value.strip()
    if "\x00" in text or len(text.encode("utf-8")) > limit:
        raise EvaluationError(f"{field} is unsafe or exceeds its size limit")
    return text


def _number(value: Any, field: str, *, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or (maximum is not None and result > maximum):
        raise EvaluationError(f"{field} is outside its allowed range")
    return result


def _optional_number(value: Any, field: str, *, maximum: float | None = None) -> float | None:
    if value is None:
        return None
    return _number(value, field, maximum=maximum)


def _nonnegative_int(value: Any, field: str, maximum: int = 10_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise EvaluationError(f"{field} must be a bounded non-negative integer")
    return value


def _digest(value: Any, field: str) -> str:
    digest = _text(value, field, limit=64) or ""
    if not _SHA256.fullmatch(digest):
        raise EvaluationError(f"{field} must be a lowercase SHA-256 digest")
    return digest


@dataclass(frozen=True)
class CandidateAssetBinding:
    reference: str
    candidate_digest: str
    diff_digest: str
    provenance_digest: str
    target: str | None
    parent_digest: str | None
    asset_key: str


def _candidate_asset_key(provenance: Mapping[str, Any], candidate_id: str) -> str:
    target = provenance.get("target")
    if isinstance(target, str) and target.strip():
        return f"target:{target.strip()}"
    name = provenance.get("new_skill_name")
    if isinstance(name, str) and name.strip():
        return f"name:{name.strip()}"
    return f"candidate:{candidate_id}"


def _bind_candidate_asset(
    path: str | Path,
    candidate_root: str | Path,
    candidate_id: str,
    expected_digest: str,
) -> CandidateAssetBinding:
    source = Path(path)
    root = Path(candidate_root)
    try:
        _reject_link_ancestors(source)
        _reject_link_ancestors(root)
    except SkillAssetError as exc:
        raise EvaluationError("candidate asset path contains a symlink or junction") from exc
    if _is_link(root) or not root.is_dir() or _is_link(source) or not source.is_dir():
        raise EvaluationError("candidate asset must be a real candidate directory")
    try:
        resolved_root = root.resolve()
        resolved_source = source.resolve()
    except OSError as exc:
        raise EvaluationError("candidate asset path is unavailable") from exc
    if resolved_source.parent != resolved_root or resolved_source.name != candidate_id:
        raise EvaluationError("candidate asset is outside the configured candidate root")
    marker = resolved_root / CANDIDATE_ROOT_MARKER
    skill_file = resolved_source / "SKILL.md"
    diff_file = resolved_source / "change.diff"
    provenance_file = resolved_source / "provenance.json"
    if any(_is_link(item) or not item.is_file() for item in (marker, skill_file, diff_file, provenance_file)):
        raise EvaluationError("candidate asset is missing its immutable candidate files")
    try:
        marker_content = read_bounded(marker, 128).decode("ascii")
        content = read_bounded(skill_file, MAX_CANDIDATE_BODY_BYTES)
        diff = read_bounded(diff_file, MAX_DIFF_BYTES)
        provenance = json.loads(read_bounded(provenance_file, MAX_CANDIDATE_PROVENANCE_BYTES).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, SkillAssetError) as exc:
        raise EvaluationError("candidate asset cannot be read") from exc
    if marker_content != "Axel Improvement Engine candidate root\n":
        raise EvaluationError("candidate asset is not below a configured candidate root")
    actual_digest = hashlib.sha256(content).hexdigest()
    if actual_digest != expected_digest:
        raise EvaluationError("candidate asset digest does not match the evaluation")
    actual_diff_digest = hashlib.sha256(diff).hexdigest()
    if (
        not isinstance(provenance, Mapping)
        or provenance.get("schema_version") != 1
        or provenance.get("candidate_id") != candidate_id
        or provenance.get("candidate_digest") != actual_digest
        or provenance.get("diff_digest") != actual_diff_digest
        or provenance.get("status") not in {"proposed", "tested", "awaiting_approval"}
        or provenance.get("provenance_digest") != provenance_digest(provenance)
    ):
        raise EvaluationError("candidate provenance does not match the evaluation")
    target = provenance.get("target")
    parent_digest = provenance.get("parent_digest")
    if target is not None and (not isinstance(target, str) or not isinstance(parent_digest, str)):
        raise EvaluationError("targeted candidate provenance is incomplete")
    if target is None and not isinstance(provenance.get("new_skill_name"), str):
        raise EvaluationError("new candidate provenance is incomplete")
    return CandidateAssetBinding(
        str(resolved_source),
        actual_digest,
        actual_diff_digest,
        str(provenance["provenance_digest"]),
        target,
        parent_digest,
        _candidate_asset_key(provenance, candidate_id),
    )


def _normalise_evidence(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationError(f"{field} must be an object")
    outcome_status = _text(value.get("outcome_status"), f"{field}.outcome_status", limit=32)
    if outcome_status not in {"success", "failure", "partial", "cancelled", "unevaluated"}:
        raise EvaluationError(f"{field}.outcome_status is unsupported")
    evaluation_status = _text(value.get("evaluation_status"), f"{field}.evaluation_status", limit=32)
    if evaluation_status not in {"evaluated", "unevaluated"}:
        raise EvaluationError(f"{field}.evaluation_status is unsupported")
    summary = _text(value.get("outcome_summary"), f"{field}.outcome_summary", limit=8_192)
    validators_raw = value.get("validators", [])
    if not isinstance(validators_raw, list) or len(validators_raw) > 100:
        raise EvaluationError(f"{field}.validators must be a bounded list")
    validators: list[dict[str, Any]] = []
    for item in validators_raw:
        if not isinstance(item, Mapping):
            raise EvaluationError(f"{field}.validators must contain objects")
        passed = item.get("passed")
        if not isinstance(passed, bool):
            raise EvaluationError(f"{field}.validator.passed must be boolean")
        validators.append(
            {
                "name": _text(item.get("name"), f"{field}.validator.name", limit=160),
                "passed": passed,
                "score": _optional_number(item.get("score"), f"{field}.validator.score", maximum=1.0),
                "details": _text(item.get("details"), f"{field}.validator.details", required=False, limit=2_048),
            }
        )
    result = {
        "outcome_status": outcome_status,
        "outcome_summary": summary,
        "evaluation_status": evaluation_status,
        "evaluator": _text(value.get("evaluator"), f"{field}.evaluator", required=False, limit=160),
        "score": _optional_number(value.get("score"), f"{field}.score", maximum=1.0),
        "validators": validators,
        "fixture_digest": _text(value.get("fixture_digest"), f"{field}.fixture_digest", required=False, limit=160),
    }
    if evaluation_status == "evaluated" and result["evaluator"] is None and not validators:
        raise EvaluationError(f"{field} has no evaluator or validator evidence")
    _canonical(result)
    return result


def _normalise_budget(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EvaluationError(f"{field} must be an object")
    result: dict[str, Any] = {}
    for key in (
        "duration_ms",
        "tokens_used",
        "context_tokens",
        "token_budget",
        "processes_used",
        "process_budget",
        "network_requests",
        "timeout_ms",
    ):
        if key in value:
            result[key] = _nonnegative_int(value[key], f"{field}.{key}")
    if "network" in value:
        result["network"] = _text(value["network"], f"{field}.network", limit=32)
    for key in ("cost", "latency_ms"):
        if key in value:
            result[key] = _number(value[key], f"{field}.{key}")
    return result


def _result_digest(
    case_id: str,
    status: str,
    reason: str,
    evidence: Mapping[str, Any],
    budget: Mapping[str, Any],
    case_digest: str | None = None,
) -> str:
    payload = {
        "case_id": case_id,
        "case_digest": case_digest,
        "status": status,
        "reason": reason,
        "evidence": evidence,
        "budget": budget,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _incomplete_result(case: ReplayCase, reason: str) -> ReplayRunResult:
    evidence = _normalise_evidence(
        {
            "outcome_status": "unevaluated",
            "outcome_summary": reason,
            "evaluation_status": "unevaluated",
        },
        "incomplete evidence",
    )
    budget = {
        "duration_ms": 0,
        "tokens_used": 0,
        "processes_used": 0,
        "network_requests": 0,
    }
    return ReplayRunResult(
        case.id,
        case.split,
        "incomplete",
        reason,
        evidence,
        budget,
        _result_digest(case.id, "incomplete", reason, evidence, budget, case.digest()),
    )


def _coerce_result(case: ReplayCase, value: Any, field: str) -> ReplayRunResult:
    if isinstance(value, ReplayRunResult):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise EvaluationError(f"{field} must be a replay result object")
    supplied_case_id = _text(value.get("case_id"), f"{field}.case_id", limit=160)
    supplied_split = _text(value.get("split"), f"{field}.split", limit=32)
    if supplied_case_id != case.id or supplied_split != case.split:
        raise EvaluationError(f"{field} identifies the wrong replay case")
    status = _text(value.get("status"), f"{field}.status", limit=32)
    if status not in VALID_RUN_STATUSES:
        raise EvaluationError(f"{field}.status is unsupported")
    reason = _text(value.get("reason", "candidate fixture result"), f"{field}.reason", limit=2_048)
    if "evidence" not in value:
        raise EvaluationError(f"{field}.evidence is required")
    evidence = _normalise_evidence(value["evidence"], f"{field}.evidence")
    if status != "incomplete" and evidence.get("fixture_digest") != case.expected_evidence.get("fixture_digest"):
        raise EvaluationError(f"{field}.evidence.fixture_digest does not match the replay case")
    budget = _normalise_budget(value.get("budget"), f"{field}.budget")
    if status == "passed" and (
        evidence["evaluation_status"] != "evaluated" or evidence["outcome_status"] == "unevaluated"
    ):
        status = "incomplete"
        reason = "candidate evidence incomplete"
    result = ReplayRunResult(
        case.id,
        case.split,
        status,
        reason or "candidate fixture result",
        evidence,
        budget,
        _result_digest(case.id, status, reason or "candidate fixture result", evidence, budget, case.digest()),
    )
    supplied_digest = _digest(value.get("result_digest"), f"{field}.result_digest")
    if supplied_digest != result.result_digest:
        raise EvaluationError(f"{field}.result_digest does not match its content")
    return result


class VariantRunner(Protocol):
    """Run one replay case for a named baseline or candidate variant."""

    def run(self, case: ReplayCase, variant: str) -> ReplayRunResult:
        ...


class DeterministicComparisonRunner:
    """Use the Ticket 5 fixture runner for baseline and validated fixtures for candidates."""

    def __init__(self, candidate_results: Mapping[str, Any] | None = None) -> None:
        self.candidate_results = dict(candidate_results or {})
        self.baseline_runner = DeterministicFixtureRunner()

    def run(self, case: ReplayCase, variant: str) -> ReplayRunResult:
        if variant == "baseline":
            return self.baseline_runner.run(case)
        if variant != "candidate":
            raise EvaluationError("variant must be baseline or candidate")
        value = self.candidate_results.get(case.id)
        if value is None:
            return _incomplete_result(case, "candidate result missing")
        return _coerce_result(case, value, f"candidate_results[{case.id}]")


@dataclass(frozen=True)
class CandidateResultBundle:
    """Candidate fixture results bound to one suite, config, and asset digest."""

    candidate_digest: str
    candidate_id: str
    suite_id: str
    seed: int
    runner_version: str
    config_digest: str
    asset_reference: str
    results: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CandidateResultBundle":
        if not isinstance(value, Mapping):
            raise EvaluationError("candidate result bundle must be an object")
        results = value.get("results")
        if not isinstance(results, Mapping):
            raise EvaluationError("candidate result bundle must contain a case-ID results mapping")
        seed = value.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise EvaluationError("candidate result bundle seed is invalid")
        candidate_id = _text(value.get("candidate_id"), "candidate result candidate_id", limit=128) or ""
        if not _SAFE_ID.fullmatch(candidate_id):
            raise EvaluationError("candidate result candidate_id is unsafe")
        return cls(
            _digest(value.get("candidate_digest"), "candidate result candidate_digest"),
            candidate_id,
            _text(value.get("suite_id"), "candidate result suite_id", limit=160) or "",
            seed,
            _text(value.get("runner_version"), "candidate result runner_version", limit=160) or "",
            _digest(value.get("config_digest"), "candidate result config_digest"),
            _text(value.get("asset_reference"), "candidate result asset_reference", limit=512) or "",
            dict(results),
        )

    def manifest(self) -> dict[str, Any]:
        return {
            "candidate_digest": self.candidate_digest,
            "candidate_id": self.candidate_id,
            "suite_id": self.suite_id,
            "seed": self.seed,
            "runner_version": self.runner_version,
            "config_digest": self.config_digest,
            "asset_reference": self.asset_reference,
        }


@dataclass(frozen=True)
class ValidatorResult:
    """Schema-validated deterministic validator output."""

    name: str
    passed: bool
    score: float | None = None
    critical: bool = False
    details: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "score": self.score,
            "critical": self.critical,
            "details": self.details,
        }


class DeterministicValidator(Protocol):
    name: str

    def validate(self, case: ReplayCase, result: ReplayRunResult) -> Mapping[str, Any]:
        ...


class ReplayStatusValidator:
    """Hard validator ensuring a replay completed without runner failure."""

    name = "replay-status"

    def validate(self, case: ReplayCase, result: ReplayRunResult) -> Mapping[str, Any]:
        passed = result.status == "passed"
        return {
            "name": self.name,
            "passed": passed,
            "score": 1.0 if passed else 0.0,
            "critical": True,
            "details": result.reason,
        }


def validate_validator_output(value: Mapping[str, Any], expected_name: str | None = None) -> ValidatorResult:
    if not isinstance(value, Mapping):
        raise EvaluationError("validator output must be an object")
    name = _text(value.get("name"), "validator.name", limit=160)
    if expected_name is not None and name != expected_name:
        raise EvaluationError("validator output name does not match its validator")
    passed = value.get("passed")
    if not isinstance(passed, bool):
        raise EvaluationError("validator output passed must be boolean")
    critical = value.get("critical", False)
    if not isinstance(critical, bool):
        raise EvaluationError("validator output critical must be boolean")
    return ValidatorResult(
        name or "",
        passed,
        _optional_number(value.get("score"), "validator.score", maximum=1.0),
        critical,
        _text(value.get("details"), "validator.details", required=False, limit=2_048),
    )


def _run_validators(
    case: ReplayCase,
    result: ReplayRunResult,
    validators: Iterable[DeterministicValidator],
) -> tuple[ValidatorResult, ...]:
    outputs: list[ValidatorResult] = []
    for validator in validators:
        name = _text(getattr(validator, "name", None), "validator.name", limit=160)
        if name is None:
            raise EvaluationError("deterministic validator has no name")
        try:
            raw = validator.validate(case, result)
        except Exception as exc:
            raise EvaluationError(f"validator '{name}' failed") from exc
        outputs.append(validate_validator_output(raw, name))
    return tuple(outputs)


class ModelJudge(Protocol):
    """Optional bounded judge; its output is evidence, never a sole gate."""

    def judge(
        self,
        case: ReplayCase,
        baseline: ReplayRunResult,
        candidate: ReplayRunResult,
    ) -> Mapping[str, Any]:
        ...


def validate_model_judge_output(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationError("model judge output must be an object")
    score = _number(value.get("score"), "model_judge.score", maximum=1.0)
    rationale = _text(value.get("rationale"), "model_judge.rationale", limit=4_096)
    critical = value.get("critical", False)
    if not isinstance(critical, bool):
        raise EvaluationError("model judge critical must be boolean")
    return {"score": score, "rationale": rationale, "critical": critical}


@dataclass(frozen=True)
class EvaluationConfig:
    """Hard gates applied to a baseline/candidate comparison."""

    min_evidence: int = 2
    require_held_out: bool = True
    min_success_delta: float = 0.0
    non_inferiority_margin: float = 0.0
    max_held_out_regressions: int = 0
    max_critical_regressions: int = 0
    max_regressions: int = 0
    max_token_overhead: int | None = None
    max_context_overhead: int | None = None
    max_cost_overhead: float | None = None
    max_candidate_cost: float | None = None
    runner_version: str = "deterministic-fixture-v1"
    provider_version: str = "none"

    def __post_init__(self) -> None:
        if self.min_evidence < 1:
            raise EvaluationError("min_evidence must be positive")
        if self.min_success_delta < 0.0 or self.non_inferiority_margin < 0.0:
            raise EvaluationError("improvement margins cannot be negative")
        if self.max_held_out_regressions < 0 or self.max_critical_regressions < 0 or self.max_regressions < 0:
            raise EvaluationError("regression limits cannot be negative")
        if self.max_token_overhead is not None and self.max_token_overhead < 0:
            raise EvaluationError("max_token_overhead cannot be negative")
        if self.max_context_overhead is not None and self.max_context_overhead < 0:
            raise EvaluationError("max_context_overhead cannot be negative")
        for value in (self.max_cost_overhead, self.max_candidate_cost):
            if value is not None and (not math.isfinite(value) or value < 0.0):
                raise EvaluationError("cost limits must be non-negative finite numbers")
        _text(self.runner_version, "runner_version", limit=160)
        _text(self.provider_version, "provider_version", limit=160)

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_evidence": self.min_evidence,
            "require_held_out": self.require_held_out,
            "min_success_delta": self.min_success_delta,
            "non_inferiority_margin": self.non_inferiority_margin,
            "max_held_out_regressions": self.max_held_out_regressions,
            "max_critical_regressions": self.max_critical_regressions,
            "max_regressions": self.max_regressions,
            "max_token_overhead": self.max_token_overhead,
            "max_context_overhead": self.max_context_overhead,
            "max_cost_overhead": self.max_cost_overhead,
            "max_candidate_cost": self.max_candidate_cost,
            "runner_version": self.runner_version,
            "provider_version": self.provider_version,
        }


@dataclass(frozen=True)
class MetricSet:
    cases: int
    task_successes: int
    task_success_rate: float | None
    validator_score: float | None
    validator_scored_cases: int
    critical_failures: int
    incomplete: int
    run_failures: int
    latency_ms: int
    tokens: int
    context_tokens: int
    cost: float | None
    missing_metrics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cases": self.cases,
            "task_successes": self.task_successes,
            "task_success_rate": self.task_success_rate,
            "validator_score": self.validator_score,
            "validator_scored_cases": self.validator_scored_cases,
            "critical_failures": self.critical_failures,
            "incomplete": self.incomplete,
            "run_failures": self.run_failures,
            "latency_ms": self.latency_ms,
            "tokens": self.tokens,
            "context_tokens": self.context_tokens,
            "cost": self.cost,
            "missing_metrics": list(self.missing_metrics),
        }


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    reason: str
    hard: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "reason": self.reason, "hard": self.hard}


@dataclass(frozen=True)
class CaseComparison:
    case_id: str
    split: str
    baseline_status: str
    candidate_status: str
    baseline_outcome_status: str
    candidate_outcome_status: str
    baseline_task_success: bool
    candidate_task_success: bool
    baseline_budget: dict[str, Any]
    candidate_budget: dict[str, Any]
    baseline_score: float | None
    candidate_score: float | None
    score_delta: float | None
    regression: bool
    critical_regression: bool
    baseline_validators: tuple[ValidatorResult, ...]
    candidate_validators: tuple[ValidatorResult, ...]
    model_judge: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "split": self.split,
            "baseline_status": self.baseline_status,
            "candidate_status": self.candidate_status,
            "baseline_outcome_status": self.baseline_outcome_status,
            "candidate_outcome_status": self.candidate_outcome_status,
            "baseline_task_success": self.baseline_task_success,
            "candidate_task_success": self.candidate_task_success,
            "baseline_budget": self.baseline_budget,
            "candidate_budget": self.candidate_budget,
            "baseline_score": self.baseline_score,
            "candidate_score": self.candidate_score,
            "score_delta": self.score_delta,
            "regression": self.regression,
            "critical_regression": self.critical_regression,
            "baseline_validators": [item.to_dict() for item in self.baseline_validators],
            "candidate_validators": [item.to_dict() for item in self.candidate_validators],
            "model_judge": self.model_judge,
        }


def _task_success(result: ReplayRunResult) -> bool:
    return result.status == "passed" and result.evidence.get("outcome_status") == "success"


def _result_score(result: ReplayRunResult, validators: Iterable[ValidatorResult]) -> float | None:
    scores = [item.score for item in validators if item.score is not None]
    return sum(scores) / len(scores) if scores else None


def _metric_set(results: Iterable[ReplayRunResult], validator_map: Mapping[str, tuple[ValidatorResult, ...]]) -> MetricSet:
    rows = tuple(results)
    scores: list[float] = []
    costs: list[float] = []
    task_successes = 0
    critical_failures = 0
    incomplete = 0
    run_failures = 0
    latency = 0
    tokens = 0
    context_tokens = 0
    missing_metrics: set[str] = set()
    for result in rows:
        validators = validator_map.get(result.case_id, ())
        if _task_success(result):
            task_successes += 1
        score = _result_score(result, validators)
        if score is not None:
            scores.append(score)
        if result.status != "passed":
            critical_failures += 1
        if result.status == "incomplete":
            incomplete += 1
        if result.status == "failed":
            run_failures += 1
        latency += int(result.budget.get("duration_ms", result.budget.get("latency_ms", 0)) or 0)
        tokens += int(result.budget.get("tokens_used", 0) or 0)
        context_tokens += int(result.budget.get("context_tokens", 0) or 0)
        if "duration_ms" not in result.budget and "latency_ms" not in result.budget:
            missing_metrics.add("duration_ms")
        if "tokens_used" not in result.budget:
            missing_metrics.add("tokens_used")
        if "context_tokens" not in result.budget:
            missing_metrics.add("context_tokens")
        if "cost" in result.budget:
            costs.append(float(result.budget["cost"]))
        else:
            missing_metrics.add("cost")
        if result.status == "passed":
            critical_failures += sum(1 for item in validators if item.critical and not item.passed)
    count = len(rows)
    return MetricSet(
        cases=count,
        task_successes=task_successes,
        task_success_rate=task_successes / count if count else None,
        validator_score=sum(scores) / len(scores) if scores else None,
        validator_scored_cases=len(scores),
        critical_failures=critical_failures,
        incomplete=incomplete,
        run_failures=run_failures,
        latency_ms=latency,
        tokens=tokens,
        context_tokens=context_tokens,
        cost=sum(costs) if costs else None,
        missing_metrics=tuple(sorted(missing_metrics)),
    )


def _delta(baseline: MetricSet, candidate: MetricSet) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in ("task_success_rate", "validator_score", "cost"):
        left = getattr(baseline, name)
        right = getattr(candidate, name)
        values[name] = right - left if left is not None and right is not None else None
    for name in ("critical_failures", "incomplete", "run_failures", "latency_ms", "tokens", "context_tokens"):
        values[name] = getattr(candidate, name) - getattr(baseline, name)
    return values


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class EvaluationRun:
    """Immutable comparison artifact and its gate decisions."""

    id: str
    candidate_id: str
    baseline_digest: str
    candidate_digest: str
    candidate_asset_reference: str
    candidate_diff_digest: str
    candidate_provenance_digest: str
    candidate_target: str | None
    candidate_parent_digest: str | None
    candidate_asset_key: str
    suite_id: str
    seed: int
    runner_version: str
    provider_version: str
    config: dict[str, Any]
    baseline: MetricSet
    candidate: MetricSet
    delta: dict[str, Any]
    comparisons: tuple[CaseComparison, ...]
    gates: tuple[GateResult, ...]
    status: str
    recommendation: str
    created_at: str
    model_judges: tuple[dict[str, Any], ...] = ()
    schema_version: int = EVALUATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "candidate_id": self.candidate_id,
            "baseline_digest": self.baseline_digest,
            "candidate_digest": self.candidate_digest,
            "candidate_asset_reference": self.candidate_asset_reference,
            "candidate_diff_digest": self.candidate_diff_digest,
            "candidate_provenance_digest": self.candidate_provenance_digest,
            "candidate_target": self.candidate_target,
            "candidate_parent_digest": self.candidate_parent_digest,
            "candidate_asset_key": self.candidate_asset_key,
            "suite_id": self.suite_id,
            "seed": self.seed,
            "runner_version": self.runner_version,
            "provider_version": self.provider_version,
            "config": self.config,
            "baseline": self.baseline.to_dict(),
            "candidate": self.candidate.to_dict(),
            "delta": self.delta,
            "comparisons": [item.to_dict() for item in self.comparisons],
            "gates": [item.to_dict() for item in self.gates],
            "status": self.status,
            "recommendation": self.recommendation,
            "created_at": self.created_at,
            "model_judges": list(self.model_judges),
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()


def _evaluation_identity(
    candidate_id: str,
    baseline_digest: str,
    candidate_digest: str,
    candidate_asset_reference: str,
    candidate_diff_digest: str,
    candidate_provenance_digest: str,
    candidate_target: str | None,
    candidate_parent_digest: str | None,
    candidate_asset_key: str,
    suite: ReplaySuite,
    config: EvaluationConfig,
    comparisons: Iterable[CaseComparison],
    created_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "baseline_digest": baseline_digest,
        "candidate_digest": candidate_digest,
        "candidate_asset_reference": candidate_asset_reference,
        "candidate_diff_digest": candidate_diff_digest,
        "candidate_provenance_digest": candidate_provenance_digest,
        "candidate_target": candidate_target,
        "candidate_parent_digest": candidate_parent_digest,
        "candidate_asset_key": candidate_asset_key,
        "suite_id": suite.id,
        "seed": suite.seed,
        "config": config.to_dict(),
        "comparisons": [item.to_dict() for item in comparisons],
        "created_at": created_at,
    }


def evaluate_candidate(
    suite: ReplaySuite,
    *,
    candidate_id: str,
    baseline_digest: str,
    candidate_digest: str,
    config: EvaluationConfig | None = None,
    candidate_results: Mapping[str, Any] | None = None,
    candidate_manifest: Mapping[str, Any] | None = None,
    candidate_asset: str | Path | None = None,
    candidate_root: str | Path | None = None,
    runner: VariantRunner | None = None,
    validators: Iterable[DeterministicValidator] | None = None,
    model_judge: ModelJudge | None = None,
    checkpoint_path: str | Path | None = None,
) -> EvaluationRun:
    """Compare two variants on exactly the same replay suite without activating either."""

    candidate_id = _text(candidate_id, "candidate_id", limit=128) or ""
    if not _SAFE_ID.fullmatch(candidate_id):
        raise EvaluationError("candidate_id is unsafe")
    baseline_digest = _text(baseline_digest, "baseline_digest", limit=160) or ""
    candidate_digest = _digest(candidate_digest, "candidate_digest")
    if not suite.cases:
        raise EvaluationError("cannot evaluate an empty replay suite")
    active_config = config or EvaluationConfig()
    if suite.runner_config.get("runner") != active_config.runner_version:
        raise EvaluationError("evaluation runner does not match the replay suite runner")
    if isinstance(candidate_results, CandidateResultBundle):
        if candidate_manifest is not None:
            raise EvaluationError("candidate manifest was supplied twice")
        candidate_manifest = candidate_results.manifest()
        supplied_results = dict(candidate_results.results)
    else:
        supplied_results = dict(candidate_results or {})
    if not isinstance(candidate_manifest, Mapping):
        raise EvaluationError("candidate result manifest is required")
    manifest = CandidateResultBundle.from_mapping({**candidate_manifest, "results": supplied_results})
    expected_config_digest = hashlib.sha256(_canonical(active_config.to_dict()).encode("utf-8")).hexdigest()
    if (
        manifest.candidate_id != candidate_id
        or manifest.candidate_digest != candidate_digest
        or manifest.suite_id != suite.id
        or manifest.seed != suite.seed
        or manifest.runner_version != active_config.runner_version
        or manifest.config_digest != expected_config_digest
    ):
        raise EvaluationError("candidate result manifest does not match the evaluation")
    if candidate_asset is None:
        raise EvaluationError("candidate asset is required")
    if candidate_root is None:
        raise EvaluationError("configured candidate root is required")
    asset = _bind_candidate_asset(candidate_asset, candidate_root, candidate_id, candidate_digest)
    asset_reference = asset.reference
    try:
        declared_asset_reference = str(Path(manifest.asset_reference).resolve())
    except OSError as exc:
        raise EvaluationError("candidate result asset_reference is invalid") from exc
    if declared_asset_reference != asset_reference:
        raise EvaluationError("candidate result asset_reference does not identify the candidate asset")
    case_ids = {case.id for case in suite.cases}
    if any(case_id not in case_ids for case_id in supplied_results):
        raise EvaluationError("candidate results contain an unknown replay case")
    active_runner = runner or DeterministicComparisonRunner(supplied_results)
    active_validators: tuple[DeterministicValidator, ...] = (ReplayStatusValidator(),) + tuple(validators or ())
    names = [getattr(item, "name", None) for item in active_validators]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise EvaluationError("deterministic validator names must be unique and non-empty")
    validator_names = tuple(name for name in names if isinstance(name, str))
    results_digest = hashlib.sha256(_canonical(supplied_results).encode("utf-8")).hexdigest()
    checkpoint_header = _checkpoint_header(
        candidate_id=candidate_id,
        baseline_digest=baseline_digest,
        candidate_digest=candidate_digest,
        candidate_asset_reference=asset_reference,
        candidate_diff_digest=asset.diff_digest,
        candidate_provenance_digest=asset.provenance_digest,
        candidate_target=asset.target,
        candidate_parent_digest=asset.parent_digest,
        candidate_asset_key=asset.asset_key,
        suite=suite,
        config=active_config,
        validator_names=validator_names,
        model_judge=model_judge,
        results_digest=results_digest,
    )
    checkpoint_rows: dict[str, dict[str, Any]] = {}
    if checkpoint_path is not None:
        checkpoint_file = Path(checkpoint_path)
        if checkpoint_file.exists():
            checkpoint_rows = _load_checkpoint(checkpoint_file, checkpoint_header, case_ids)
        else:
            _write_checkpoint(checkpoint_file, checkpoint_header, checkpoint_rows)

    baseline_results: list[ReplayRunResult] = []
    candidate_rows: list[ReplayRunResult] = []
    baseline_validator_map: dict[str, tuple[ValidatorResult, ...]] = {}
    candidate_validator_map: dict[str, tuple[ValidatorResult, ...]] = {}
    comparisons: list[CaseComparison] = []
    judge_rows: list[dict[str, Any]] = []
    for case in suite.cases:
        saved = checkpoint_rows.get(case.id)
        if saved is not None:
            baseline_result, candidate_result = _restore_checkpoint_row(case, saved)
            try:
                verified_baseline = active_runner.run(case, "baseline")
            except EvaluationError:
                raise
            except Exception:
                verified_baseline = _incomplete_result(case, "runner failure")
            verified_baseline = _coerce_result(case, verified_baseline, "resumed baseline result")
            try:
                verified_candidate = active_runner.run(case, "candidate")
            except EvaluationError:
                raise
            except Exception:
                verified_candidate = _incomplete_result(case, "runner failure")
            verified_candidate = _coerce_result(case, verified_candidate, "resumed candidate result")
            if (
                verified_baseline.result_digest != baseline_result.result_digest
                or verified_candidate.result_digest != candidate_result.result_digest
            ):
                raise EvaluationError("evaluation checkpoint results do not match the current runner")
            baseline_validators = _run_validators(case, baseline_result, active_validators)
            candidate_validators = _run_validators(case, candidate_result, active_validators)
            judge_value = None
            if model_judge is not None:
                try:
                    judge_value = validate_model_judge_output(model_judge.judge(case, baseline_result, candidate_result))
                except Exception as exc:
                    raise EvaluationError("model judge output failed validation") from exc
        else:
            try:
                baseline_result = active_runner.run(case, "baseline")
            except EvaluationError:
                raise
            except Exception:
                baseline_result = _incomplete_result(case, "runner failure")
            baseline_result = _coerce_result(case, baseline_result, "baseline result")
            try:
                candidate_result = active_runner.run(case, "candidate")
            except EvaluationError:
                raise
            except Exception:
                candidate_result = _incomplete_result(case, "runner failure")
            candidate_result = _coerce_result(case, candidate_result, "candidate result")
            baseline_validators = _run_validators(case, baseline_result, active_validators)
            candidate_validators = _run_validators(case, candidate_result, active_validators)
            judge_value = None
            if model_judge is not None:
                try:
                    judge_value = validate_model_judge_output(model_judge.judge(case, baseline_result, candidate_result))
                except Exception as exc:
                    raise EvaluationError("model judge output failed validation") from exc
            if checkpoint_path is not None:
                checkpoint_rows[case.id] = _checkpoint_row(
                    case,
                    baseline_result,
                    candidate_result,
                )
                _write_checkpoint(checkpoint_path, checkpoint_header, checkpoint_rows)
        baseline_validator_map[case.id] = baseline_validators
        candidate_validator_map[case.id] = candidate_validators
        baseline_results.append(baseline_result)
        candidate_rows.append(candidate_result)
        baseline_score = _result_score(baseline_result, baseline_validators)
        candidate_score = _result_score(candidate_result, candidate_validators)
        score_delta = candidate_score - baseline_score if baseline_score is not None and candidate_score is not None else None
        regression = (
            (_task_success(baseline_result) and not _task_success(candidate_result))
            or (baseline_result.status == "passed" and candidate_result.status != "passed")
            or (score_delta is not None and score_delta < 0.0)
        )
        critical_regression = regression and case.split == "held-out"
        critical_regression = critical_regression or any(
            item.critical and not item.passed for item in candidate_validators
        )
        if judge_value is not None:
            judge_rows.append({"case_id": case.id, **judge_value})
        comparisons.append(
            CaseComparison(
                case.id,
                case.split,
                baseline_result.status,
                candidate_result.status,
                baseline_result.evidence["outcome_status"],
                candidate_result.evidence["outcome_status"],
                _task_success(baseline_result),
                _task_success(candidate_result),
                dict(baseline_result.budget),
                dict(candidate_result.budget),
                baseline_score,
                candidate_score,
                score_delta,
                regression,
                critical_regression,
                baseline_validators,
                candidate_validators,
                judge_value,
            )
        )

    baseline_metrics = _metric_set(baseline_results, baseline_validator_map)
    candidate_metrics = _metric_set(candidate_rows, candidate_validator_map)
    delta = _delta(baseline_metrics, candidate_metrics)
    held_out = tuple(case for case in suite.cases if case.split == "held-out")
    development = tuple(case for case in suite.cases if case.split == "development")
    critical_regressions = sum(item.critical_regression for item in comparisons)
    held_out_regressions = sum(item.regression and item.split == "held-out" for item in comparisons)
    regressions = sum(item.regression for item in comparisons)
    same_cases = len(baseline_results) == len(candidate_rows) == len(suite.cases)
    complete = baseline_metrics.incomplete == 0 and candidate_metrics.incomplete == 0
    validator_gate = all(item.passed for values in candidate_validator_map.values() for item in values)
    metric_gate = (
        delta["task_success_rate"] is not None
        and delta["task_success_rate"] >= active_config.min_success_delta
        and delta["validator_score"] is not None
        and delta["validator_score"] >= -active_config.non_inferiority_margin
    )
    required_metrics = {"duration_ms", "tokens_used"}
    if active_config.max_context_overhead is not None:
        required_metrics.add("context_tokens")
    missing_metrics = required_metrics & (
        set(baseline_metrics.missing_metrics) | set(candidate_metrics.missing_metrics)
    )
    if active_config.max_cost_overhead is not None and "cost" in (
        set(baseline_metrics.missing_metrics) | set(candidate_metrics.missing_metrics)
    ):
        missing_metrics.add("cost")
    if active_config.max_candidate_cost is not None and "cost" in candidate_metrics.missing_metrics:
        missing_metrics.add("candidate.cost")
    metric_completeness = not missing_metrics
    cost_gate = True
    cost_reason = "cost budget not configured"
    if active_config.max_token_overhead is not None:
        cost_gate = cost_gate and delta["tokens"] <= active_config.max_token_overhead
        cost_reason = f"token overhead {delta['tokens']} within budget {active_config.max_token_overhead}"
    if active_config.max_context_overhead is not None:
        cost_gate = cost_gate and delta["context_tokens"] <= active_config.max_context_overhead
        cost_reason = f"context overhead {delta['context_tokens']} within budget {active_config.max_context_overhead}"
    if active_config.max_cost_overhead is not None:
        cost_gate = cost_gate and delta["cost"] is not None and delta["cost"] <= active_config.max_cost_overhead
        cost_reason = f"cost overhead {delta['cost']} within budget {active_config.max_cost_overhead}"
    if active_config.max_candidate_cost is not None:
        cost_gate = cost_gate and candidate_metrics.cost is not None and candidate_metrics.cost <= active_config.max_candidate_cost
        cost_reason = f"candidate cost {candidate_metrics.cost} within budget {active_config.max_candidate_cost}"
    evidence_enough = len(suite.cases) >= active_config.min_evidence
    if active_config.require_held_out:
        evidence_enough = evidence_enough and bool(held_out) and bool(development)
    gates = (
        GateResult("same_cases", same_cases, "baseline and candidate use the suite case set" if same_cases else "case sets differ"),
        GateResult("minimum_evidence", evidence_enough, "suite has enough development and held-out evidence" if evidence_enough else "insufficient development or held-out evidence"),
        GateResult("complete_runs", complete, "all selected runs completed" if complete else "incomplete or interrupted run cannot pass"),
        GateResult(
            "metric_completeness",
            metric_completeness,
            "required evaluation metrics are present"
            if metric_completeness
            else f"required evaluation metrics are missing: {', '.join(sorted(missing_metrics))}",
        ),
        GateResult("deterministic_validators", validator_gate, "all hard validator results passed" if validator_gate else "a deterministic validator failed"),
        GateResult("held_out_regressions", held_out_regressions <= active_config.max_held_out_regressions, f"held-out regressions={held_out_regressions}, limit={active_config.max_held_out_regressions}"),
        GateResult("critical_regressions", critical_regressions <= active_config.max_critical_regressions, f"critical regressions={critical_regressions}, limit={active_config.max_critical_regressions}"),
        GateResult("regressions", regressions <= active_config.max_regressions, f"regressions={regressions}, limit={active_config.max_regressions}"),
        GateResult("improvement_or_non_inferiority", metric_gate, f"success delta={delta['task_success_rate']}, validator delta={delta['validator_score']}"),
        GateResult("cost_and_token_budget", cost_gate, cost_reason),
    )
    if not evidence_enough:
        status = "incubating"
        recommendation = "collect more replay evidence before evaluation can pass"
    elif all(gate.passed for gate in gates):
        status = "eligible"
        recommendation = "eligible for human approval; not active"
    else:
        status = "rejected"
        recommendation = "reject candidate; retain evidence for review"
    created_at = _now()
    identity = _evaluation_identity(
        candidate_id,
        baseline_digest,
        candidate_digest,
        asset_reference,
        asset.diff_digest,
        asset.provenance_digest,
        asset.target,
        asset.parent_digest,
        asset.asset_key,
        suite,
        active_config,
        comparisons,
        created_at,
    )
    evaluation_id = "evaluation-" + hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()[:24]
    return EvaluationRun(
        id=evaluation_id,
        candidate_id=candidate_id,
        baseline_digest=baseline_digest,
        candidate_digest=candidate_digest,
        candidate_asset_reference=asset_reference,
        candidate_diff_digest=asset.diff_digest,
        candidate_provenance_digest=asset.provenance_digest,
        candidate_target=asset.target,
        candidate_parent_digest=asset.parent_digest,
        candidate_asset_key=asset.asset_key,
        suite_id=suite.id,
        seed=suite.seed,
        runner_version=active_config.runner_version,
        provider_version=active_config.provider_version,
        config=active_config.to_dict(),
        baseline=baseline_metrics,
        candidate=candidate_metrics,
        delta=delta,
        comparisons=tuple(comparisons),
        gates=gates,
        status=status,
        recommendation=recommendation,
        created_at=created_at,
        model_judges=tuple(judge_rows),
    )


def default_evaluation_path(root: str | Path, evaluation: EvaluationRun) -> Path:
    return Path(root) / "data" / "evaluations" / f"{evaluation.id}.json"


def _reject_protected_path(path: Path, protected_roots: Iterable[str | Path]) -> None:
    target = Path(os.path.abspath(path)).resolve()
    for root in protected_roots:
        protected = Path(os.path.abspath(root)).resolve()
        if target == protected or protected in target.parents or target in protected.parents:
            raise EvaluationError("evaluation artifact path overlaps a protected asset root")


def _effective_protected_roots(path: Path, protected_roots: Iterable[str | Path]) -> tuple[Path, ...]:
    roots = {Path(item) for item in protected_roots}
    absolute = Path(os.path.abspath(path))
    for ancestor in (absolute, *absolute.parents):
        if ancestor.name == "skills":
            roots.update(ancestor / name for name in ("active", "candidates", "approved"))
    return tuple(roots)


def _checkpoint_header(
    *,
    candidate_id: str,
    baseline_digest: str,
    candidate_digest: str,
    candidate_asset_reference: str,
    candidate_diff_digest: str,
    candidate_provenance_digest: str,
    candidate_target: str | None,
    candidate_parent_digest: str | None,
    candidate_asset_key: str,
    suite: ReplaySuite,
    config: EvaluationConfig,
    validator_names: Iterable[str],
    model_judge: ModelJudge | None,
    results_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "baseline_digest": baseline_digest,
        "candidate_digest": candidate_digest,
        "candidate_asset_reference": candidate_asset_reference,
        "candidate_diff_digest": candidate_diff_digest,
        "candidate_provenance_digest": candidate_provenance_digest,
        "candidate_target": candidate_target,
        "candidate_parent_digest": candidate_parent_digest,
        "candidate_asset_key": candidate_asset_key,
        "suite_id": suite.id,
        "seed": suite.seed,
        "runner_version": config.runner_version,
        "provider_version": config.provider_version,
        "config_digest": hashlib.sha256(_canonical(config.to_dict()).encode("utf-8")).hexdigest(),
        "validator_names": list(validator_names),
        "model_judge": model_judge is not None,
        "results_digest": results_digest,
    }


def _checkpoint_row(
    case: ReplayCase,
    baseline: ReplayRunResult,
    candidate: ReplayRunResult,
) -> dict[str, Any]:
    return {
        "case_id": case.id,
        "baseline": baseline.to_dict(),
        "candidate": candidate.to_dict(),
    }


def _restore_checkpoint_row(
    case: ReplayCase,
    value: Any,
) -> tuple[ReplayRunResult, ReplayRunResult]:
    if not isinstance(value, Mapping):
        raise EvaluationError("evaluation checkpoint case must be an object")
    if value.get("case_id") != case.id:
        raise EvaluationError("evaluation checkpoint case identity is invalid")
    baseline = _coerce_result(case, value.get("baseline"), "checkpoint baseline result")
    candidate = _coerce_result(case, value.get("candidate"), "checkpoint candidate result")
    return baseline, candidate


def _write_checkpoint(path: str | Path, header: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]) -> None:
    target = Path(path)
    _reject_protected_path(target, _effective_protected_roots(target, ()))
    try:
        _reject_link_ancestors(target)
    except SkillAssetError as exc:
        raise EvaluationError("evaluation checkpoint path contains a symlink or junction") from exc
    if _is_link(target):
        raise EvaluationError("evaluation checkpoint target is a symlink or junction")
    payload = {**dict(header), "completed": [rows[key] for key in sorted(rows)]}
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > MAX_EVALUATION_BYTES:
        raise EvaluationError("evaluation checkpoint exceeds size limit")
    atomic_write_text(target, encoded)


def _load_checkpoint(
    path: str | Path,
    expected_header: Mapping[str, Any],
    case_ids: set[str],
) -> dict[str, dict[str, Any]]:
    source = Path(path)
    _reject_protected_path(source, _effective_protected_roots(source, ()))
    try:
        _reject_link_ancestors(source)
        if _is_link(source) or not source.is_file():
            raise EvaluationError("evaluation checkpoint is unavailable")
        if source.stat().st_size > MAX_EVALUATION_BYTES:
            raise EvaluationError("evaluation checkpoint exceeds size limit")
        value = json.loads(source.read_text(encoding="utf-8"))
    except EvaluationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationError("evaluation checkpoint is invalid") from exc
    if not isinstance(value, Mapping):
        raise EvaluationError("evaluation checkpoint is invalid")
    actual_header = {key: value.get(key) for key in expected_header}
    if actual_header != dict(expected_header):
        raise EvaluationError("evaluation checkpoint does not match the evaluation")
    completed = value.get("completed")
    if not isinstance(completed, list) or len(completed) > MAX_CASES:
        raise EvaluationError("evaluation checkpoint cases are invalid")
    rows: dict[str, dict[str, Any]] = {}
    for item in completed:
        if not isinstance(item, Mapping):
            raise EvaluationError("evaluation checkpoint case is invalid")
        case_id = item.get("case_id")
        if not isinstance(case_id, str) or case_id not in case_ids or case_id in rows:
            raise EvaluationError("evaluation checkpoint case identity is invalid")
        rows[case_id] = dict(item)
    return rows


def write_evaluation(
    path: str | Path,
    evaluation: EvaluationRun,
    *,
    protected_roots: Iterable[str | Path] = (),
) -> Path:
    target = Path(path)
    _reject_protected_path(target, _effective_protected_roots(target, protected_roots))
    try:
        _reject_link_ancestors(target)
    except SkillAssetError as exc:
        raise EvaluationError("evaluation path contains a symlink or junction") from exc
    if _is_link(target):
        raise EvaluationError("evaluation target is a symlink or junction")
    payload = json.dumps(evaluation.to_dict(), ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    if len(payload.encode("utf-8")) > MAX_EVALUATION_BYTES:
        raise EvaluationError("evaluation artifact exceeds size limit")
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise EvaluationError("existing evaluation artifact cannot be read") from exc
        if existing != payload:
            raise EvaluationError("evaluation artifacts are immutable")
        return target
    atomic_write_text(target, payload)
    return target


def load_candidate_results(path: str | Path) -> CandidateResultBundle:
    source = Path(path)
    try:
        _reject_link_ancestors(source)
        if _is_link(source) or not source.is_file():
            raise EvaluationError("candidate result file is unavailable")
        if source.stat().st_size > MAX_EVALUATION_BYTES:
            raise EvaluationError("candidate result file exceeds size limit")
        value = json.loads(source.read_text(encoding="utf-8"))
    except EvaluationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationError("candidate result file is invalid") from exc
    return CandidateResultBundle.from_mapping(value)


class ChampionRegistry:
    """Record the best eligible tested digest without touching active assets."""

    def __init__(self, path: str | Path, *, protected_roots: Iterable[str | Path] = ()):
        self.path = Path(path)
        self.protected_roots = tuple(protected_roots)

    def record(self, evaluation: EvaluationRun, *, target: str = "default") -> dict[str, Any]:
        if evaluation.status != "eligible":
            raise EvaluationError("only eligible evaluations can become champions")
        target = _text(target, "champion target", limit=160) or "default"
        if not _SAFE_ID.fullmatch(target):
            raise EvaluationError("champion target is unsafe")
        _reject_protected_path(self.path, _effective_protected_roots(self.path, self.protected_roots))
        try:
            _reject_link_ancestors(self.path)
        except SkillAssetError as exc:
            raise EvaluationError("champion registry path contains a symlink or junction") from exc
        if _is_link(self.path):
            raise EvaluationError("champion registry target is a symlink or junction")
        current: dict[str, Any] | None = None
        if self.path.exists():
            try:
                current = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise EvaluationError("champion registry is invalid") from exc
            if not isinstance(current, Mapping):
                raise EvaluationError("champion registry is invalid")
            if current.get("target") != target:
                raise EvaluationError("champion registry target does not match the requested target")
        candidate_score = (
            evaluation.candidate.task_success_rate or 0.0,
            evaluation.candidate.validator_score or 0.0,
        )
        current_score = tuple(current.get("score", [0.0, 0.0])) if isinstance(current, Mapping) else (0.0, 0.0)
        if len(current_score) != 2 or any(not isinstance(item, (int, float)) for item in current_score):
            raise EvaluationError("champion registry score is invalid")
        if current is not None and tuple(float(item) for item in current_score) > candidate_score:
            return dict(current)
        record = {
            "schema_version": 1,
            "target": target,
            "candidate_id": evaluation.candidate_id,
            "candidate_digest": evaluation.candidate_digest,
            "evaluation_id": evaluation.id,
            "evaluation_digest": evaluation.digest(),
            "score": [candidate_score[0], candidate_score[1]],
            "recorded_at": _now(),
        }
        atomic_write_text(self.path, json.dumps(record, ensure_ascii=True, sort_keys=True, indent=2) + "\n")
        return record

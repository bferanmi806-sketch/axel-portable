"""Explicit approval, Git-backed promotion, and rollback of candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping
from typing import Any

from .candidates import (
    CANDIDATE_ROOT_MARKER,
    MAX_CANDIDATE_BODY_BYTES,
    MAX_DIFF_BYTES,
    MAX_PROVENANCE_BYTES,
    _candidate_lock,
    _transition_candidate_unlocked,
    provenance_digest,
)
from .skills import SkillAssetError, SkillBank, _is_link, _reject_link_ancestors, content_digest, read_bounded


PROMOTION_SCHEMA_VERSION = 1
MAX_EVALUATION_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
_SAFE_CANDIDATE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REQUIRED_EVALUATION_GATES = frozenset(
    {
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
    }
)
_CANDIDATE_STATUSES = frozenset(
    {
        "observed",
        "diagnosed",
        "proposed",
        "tested",
        "awaiting_approval",
        "approved",
        "rejected",
        "retired",
        "rolled_back",
        "suppressed",
    }
)


class PromotionError(ValueError):
    """Raised when approval or Git-backed promotion cannot proceed safely."""


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise PromotionError("promotion data must be JSON-compatible") from exc


def _text(value: Any, field: str, *, limit: int = 8_192) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PromotionError(f"{field} must be a non-empty string")
    text = value.strip()
    if "\x00" in text or len(text.encode("utf-8")) > limit:
        raise PromotionError(f"{field} is unsafe or exceeds its size limit")
    return text


def _digest(value: Any, field: str) -> str:
    digest = _text(value, field, limit=64)
    if not _SHA256.fullmatch(digest):
        raise PromotionError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@contextmanager
def _manifest_lock(approved_root: Path):
    lock = approved_root / ".promotion.lock"
    descriptor: int | None = None
    acquired = False
    deadline = time.monotonic() + 2.0
    try:
        while True:
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(descriptor, str(os.getpid()).encode("ascii"))
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = None
                acquired = True
                break
            except FileExistsError as exc:
                stale = False
                try:
                    if time.time() - lock.stat().st_mtime > 60:
                        owner_text = lock.read_text(encoding="ascii").strip()
                        owner = int(owner_text)
                        try:
                            os.kill(owner, 0)
                        except (OSError, ProcessLookupError):
                            stale = True
                except (OSError, ValueError):
                    stale = time.time() - lock.stat().st_mtime > 60 if lock.exists() else False
                if stale:
                    try:
                        lock.unlink(missing_ok=True)
                        continue
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise PromotionError("approved manifest is busy") from exc
                time.sleep(0.02)
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if acquired:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = handle.name
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as exc:
        raise PromotionError(f"failed to write {path.name}") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_write_bytes(path, (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def _safe_root(path: str | Path, field: str, *, create: bool = False) -> Path:
    root = Path(path)
    try:
        _reject_link_ancestors(root)
    except Exception as exc:
        raise PromotionError(f"{field} contains a symlink or junction") from exc
    if not root.exists():
        if not create:
            raise PromotionError(f"{field} is unavailable")
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PromotionError(f"{field} cannot be created") from exc
    if _is_link(root) or not root.is_dir():
        raise PromotionError(f"{field} must be a real directory")
    try:
        return root.resolve()
    except OSError as exc:
        raise PromotionError(f"{field} is unavailable") from exc


def _safe_optional_root(path: str | Path, field: str) -> Path:
    root = Path(path)
    try:
        _reject_link_ancestors(root)
    except SkillAssetError as exc:
        raise PromotionError(f"{field} contains a symlink or junction") from exc
    if root.exists() and (_is_link(root) or not root.is_dir()):
        raise PromotionError(f"{field} must be a real directory")
    try:
        return root.resolve()
    except OSError as exc:
        raise PromotionError(f"{field} is unavailable") from exc


def _read_json(path: Path, limit: int, field: str) -> dict[str, Any]:
    try:
        _reject_link_ancestors(path)
        if _is_link(path) or not path.is_file():
            raise PromotionError(f"{field} is unavailable")
        if path.stat().st_size > limit:
            raise PromotionError(f"{field} exceeds its size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except PromotionError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, SkillAssetError) as exc:
        raise PromotionError(f"{field} is invalid") from exc
    if not isinstance(value, dict):
        raise PromotionError(f"{field} must be an object")
    return value


def _require_child(root: Path, name: str, field: str) -> Path:
    path = root / name
    if path.parent != root or _is_link(path):
        raise PromotionError(f"{field} path is unsafe")
    return path


@dataclass(frozen=True)
class _CandidateRecord:
    root: Path
    path: Path
    skill: bytes
    diff: bytes
    provenance: dict[str, Any]
    status: str
    candidate_digest: str
    diff_digest: str
    provenance_digest: str
    asset_key: str


@dataclass(frozen=True)
class ApprovalResult:
    candidate_id: str
    candidate_digest: str
    evaluation_id: str
    evaluation_digest: str
    decision: str
    status: str
    approval_path: Path
    approval_digest: str
    decided_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_digest": self.candidate_digest,
            "evaluation_id": self.evaluation_id,
            "evaluation_digest": self.evaluation_digest,
            "decision": self.decision,
            "status": self.status,
            "approval_path": str(self.approval_path),
            "approval_digest": self.approval_digest,
            "decided_at": self.decided_at,
        }


@dataclass(frozen=True)
class PromotionResult:
    candidate_id: str
    candidate_digest: str
    evaluation_id: str
    asset_key: str
    approved_path: Path
    manifest_path: Path
    commit: str
    previous_candidate_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_digest": self.candidate_digest,
            "evaluation_id": self.evaluation_id,
            "asset_key": self.asset_key,
            "approved_path": str(self.approved_path),
            "manifest_path": str(self.manifest_path),
            "commit": self.commit,
            "previous_candidate_id": self.previous_candidate_id,
        }


@dataclass(frozen=True)
class RollbackResult:
    candidate_id: str
    candidate_digest: str
    asset_key: str
    restored_candidate_id: str | None
    restored_digest: str | None
    manifest_path: Path
    commit: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_digest": self.candidate_digest,
            "asset_key": self.asset_key,
            "restored_candidate_id": self.restored_candidate_id,
            "restored_digest": self.restored_digest,
            "manifest_path": str(self.manifest_path),
            "commit": self.commit,
        }


def _asset_key(provenance: Mapping[str, Any], candidate_id: str) -> str:
    target = provenance.get("target")
    if isinstance(target, str) and target.strip():
        key = f"target:{target.strip()}"
    else:
        name = provenance.get("new_skill_name")
        key = f"name:{name.strip()}" if isinstance(name, str) and name.strip() else f"candidate:{candidate_id}"
    return _text(key, "candidate asset key", limit=512)

def _load_candidate(candidate_root: str | Path, candidate_id: str) -> _CandidateRecord:
    if not _SAFE_CANDIDATE_ID.fullmatch(candidate_id):
        raise PromotionError("candidate_id is unsafe")
    root = _safe_root(candidate_root, "candidate root")
    marker = _require_child(root, CANDIDATE_ROOT_MARKER, "candidate root marker")
    try:
        marker_content = read_bounded(marker, 128).decode("ascii")
    except (OSError, SkillAssetError, UnicodeDecodeError) as exc:
        raise PromotionError("candidate root marker is unavailable") from exc
    if marker_content != "Axel Improvement Engine candidate root\n":
        raise PromotionError("candidate root marker is invalid")
    path = _require_child(root, candidate_id, "candidate")
    if not path.is_dir() or _is_link(path):
        raise PromotionError("candidate directory is unavailable")
    skill_path = _require_child(path, "SKILL.md", "candidate skill")
    diff_path = _require_child(path, "change.diff", "candidate diff")
    provenance_path = _require_child(path, "provenance.json", "candidate provenance")
    try:
        skill = read_bounded(skill_path, MAX_CANDIDATE_BODY_BYTES)
        diff = read_bounded(diff_path, MAX_DIFF_BYTES)
    except (OSError, SkillAssetError) as exc:
        raise PromotionError("candidate assets cannot be read") from exc
    provenance = _read_json(provenance_path, MAX_PROVENANCE_BYTES, "candidate provenance")
    status = _text(provenance.get("status"), "candidate status", limit=32)
    if status not in _CANDIDATE_STATUSES:
        raise PromotionError("candidate status is unsupported")
    if provenance.get("schema_version") != 1 or provenance.get("candidate_id") != candidate_id:
        raise PromotionError("candidate provenance identity is invalid")
    candidate_digest = _digest(provenance.get("candidate_digest"), "candidate provenance candidate_digest")
    diff_digest = _digest(provenance.get("diff_digest"), "candidate provenance diff_digest")
    stored_provenance_digest = _digest(provenance.get("provenance_digest"), "candidate provenance provenance_digest")
    if content_digest(skill) != candidate_digest:
        raise PromotionError("candidate skill digest does not match provenance")
    if content_digest(diff) != diff_digest:
        raise PromotionError("candidate diff digest does not match provenance")
    if provenance_digest(provenance) != stored_provenance_digest:
        raise PromotionError("candidate provenance digest does not match provenance")
    return _CandidateRecord(
        root,
        path,
        skill,
        diff,
        provenance,
        status,
        candidate_digest,
        diff_digest,
        stored_provenance_digest,
        _asset_key(provenance, candidate_id),
    )


def _load_evaluation(path: str | Path, candidate: _CandidateRecord, expected_digest: str | None = None) -> tuple[dict[str, Any], str]:
    evaluation_path = Path(path)
    evaluation = _read_json(evaluation_path, MAX_EVALUATION_BYTES, "evaluation artifact")
    evaluation_digest = hashlib.sha256(_canonical(evaluation).encode("utf-8")).hexdigest()
    if expected_digest is not None and evaluation_digest != expected_digest:
        raise PromotionError("evaluation artifact changed after approval")
    if (
        evaluation.get("schema_version") != 1
        or evaluation.get("status") != "eligible"
        or evaluation.get("candidate_id") != candidate.provenance.get("candidate_id")
        or evaluation.get("candidate_digest") != candidate.candidate_digest
        or evaluation.get("candidate_asset_reference") != str(candidate.path.resolve())
        or evaluation.get("candidate_diff_digest") != candidate.diff_digest
        or evaluation.get("candidate_provenance_digest") != candidate.provenance_digest
        or evaluation.get("candidate_target") != candidate.provenance.get("target")
        or evaluation.get("candidate_parent_digest") != candidate.provenance.get("parent_digest")
        or evaluation.get("candidate_asset_key") != candidate.asset_key
    ):
        raise PromotionError("evaluation is not eligible for this exact candidate")
    evaluation_id = _text(evaluation.get("id"), "evaluation id", limit=160)
    gates = evaluation.get("gates")
    gate_names = [item.get("name") if isinstance(item, Mapping) else None for item in gates] if isinstance(gates, list) else []
    if (
        not isinstance(gates, list)
        or set(gate_names) != _REQUIRED_EVALUATION_GATES
        or len(gate_names) != len(_REQUIRED_EVALUATION_GATES)
        or any(
            not isinstance(item, Mapping)
            or item.get("passed") is not True
            or item.get("hard") is not True
            for item in gates
        )
    ):
        raise PromotionError("evaluation contains a failed or incomplete gate")
    candidate_metrics = evaluation.get("candidate")
    comparisons = evaluation.get("comparisons")
    baseline_metrics = evaluation.get("baseline")
    if (
        not isinstance(candidate_metrics, Mapping)
        or not isinstance(baseline_metrics, Mapping)
        or candidate_metrics.get("incomplete") != 0
        or candidate_metrics.get("run_failures") != 0
        or not isinstance(comparisons, list)
        or not comparisons
        or candidate_metrics.get("cases") != len(comparisons)
    ):
        raise PromotionError("evaluation contains incomplete candidate runs")
    _validate_evaluation_semantics(evaluation, baseline_metrics, candidate_metrics, comparisons, gates)
    evaluation["id"] = evaluation_id
    return evaluation, evaluation_digest


def _metric_int(metrics: Mapping[str, Any], name: str) -> int:
    value = metrics.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PromotionError(f"evaluation metric {name} is invalid")
    return value


def _metric_rate(metrics: Mapping[str, Any], name: str) -> float | None:
    value = metrics.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise PromotionError(f"evaluation metric {name} is invalid")
    return float(value)


def _config_nonnegative(config: Mapping[str, Any], name: str, *, integer: bool = False) -> int | float | None:
    value = config.get(name)
    if value is None:
        return None
    if integer:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PromotionError(f"evaluation config {name} is invalid")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0.0:
        raise PromotionError(f"evaluation config {name} is invalid")
    return float(value)


def _validate_evaluation_semantics(
    evaluation: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    comparisons: list[Any],
    gates: list[Any],
) -> None:
    config = evaluation.get("config")
    delta = evaluation.get("delta")
    if not isinstance(config, Mapping) or not isinstance(delta, Mapping):
        raise PromotionError("evaluation configuration or delta is missing")
    baseline_cases = _metric_int(baseline, "cases")
    candidate_cases = _metric_int(candidate, "cases")
    if baseline_cases != candidate_cases or candidate_cases != len(comparisons) or candidate_cases == 0:
        raise PromotionError("evaluation case metrics are inconsistent")

    valid_statuses = {"passed", "failed", "incomplete"}
    valid_outcomes = {"success", "failure", "partial", "cancelled", "unevaluated"}

    def validate_budget(value: Any, field: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise PromotionError(f"{field} budget is invalid")
        budget = dict(value)
        for name in ("duration_ms", "tokens_used", "context_tokens"):
            if name in budget and (isinstance(budget[name], bool) or not isinstance(budget[name], int) or budget[name] < 0):
                raise PromotionError(f"{field} budget {name} is invalid")
        if "latency_ms" in budget and (
            isinstance(budget["latency_ms"], bool)
            or not isinstance(budget["latency_ms"], (int, float))
            or not math.isfinite(float(budget["latency_ms"]))
            or float(budget["latency_ms"]) < 0.0
        ):
            raise PromotionError(f"{field} budget latency_ms is invalid")
        if "cost" in budget and (
            isinstance(budget["cost"], bool)
            or not isinstance(budget["cost"], (int, float))
            or not math.isfinite(float(budget["cost"]))
            or float(budget["cost"]) < 0.0
        ):
            raise PromotionError(f"{field} budget cost is invalid")
        return budget

    def validate_validators(value: Any, field: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise PromotionError(f"{field} validator evidence is missing")
        result: list[dict[str, Any]] = []
        for validator in value:
            if not isinstance(validator, Mapping) or not isinstance(validator.get("name"), str) or not validator["name"].strip():
                raise PromotionError(f"{field} validator evidence is invalid")
            if not isinstance(validator.get("passed"), bool) or not isinstance(validator.get("critical"), bool):
                raise PromotionError(f"{field} validator evidence is invalid")
            score = validator.get("score")
            if score is not None and (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or not 0.0 <= float(score) <= 1.0
            ):
                raise PromotionError(f"{field} validator score is invalid")
            result.append(dict(validator))
        return result

    def derived_metrics(side: str) -> dict[str, Any]:
        successes = 0
        scored: list[float] = []
        critical_failures = 0
        incomplete = 0
        run_failures = 0
        latency = 0
        tokens = 0
        context_tokens = 0
        costs: list[float] = []
        missing: set[str] = set()
        for comparison in comparisons:
            status = comparison[f"{side}_status"]
            outcome = comparison[f"{side}_outcome_status"]
            budget = comparison[f"{side}_budget"]
            validators = comparison[f"{side}_validators"]
            if status not in valid_statuses or outcome not in valid_outcomes:
                raise PromotionError("evaluation comparison outcome is invalid")
            budget = validate_budget(budget, f"comparison {comparison['case_id']} {side}")
            validators = validate_validators(validators, f"comparison {comparison['case_id']} {side}")
            task_success = status == "passed" and outcome == "success"
            if comparison[f"{side}_task_success"] is not task_success:
                raise PromotionError("evaluation task-success fields do not match outcome evidence")
            if task_success:
                successes += 1
            scores = [float(item["score"]) for item in validators if item.get("score") is not None]
            if scores:
                scored.append(sum(scores) / len(scores))
            if status != "passed":
                critical_failures += 1
            if status == "incomplete":
                incomplete += 1
            if status == "failed":
                run_failures += 1
            latency += int(budget.get("duration_ms", budget.get("latency_ms", 0)) or 0)
            tokens += int(budget.get("tokens_used", 0) or 0)
            context_tokens += int(budget.get("context_tokens", 0) or 0)
            if "duration_ms" not in budget and "latency_ms" not in budget:
                missing.add("duration_ms")
            if "tokens_used" not in budget:
                missing.add("tokens_used")
            if "context_tokens" not in budget:
                missing.add("context_tokens")
            if "cost" in budget:
                costs.append(float(budget["cost"]))
            else:
                missing.add("cost")
            if status == "passed":
                critical_failures += sum(1 for item in validators if item["critical"] and not item["passed"])
        count = len(comparisons)
        return {
            "cases": count,
            "task_successes": successes,
            "task_success_rate": successes / count,
            "validator_score": sum(scored) / len(scored) if scored else None,
            "validator_scored_cases": len(scored),
            "critical_failures": critical_failures,
            "incomplete": incomplete,
            "run_failures": run_failures,
            "latency_ms": latency,
            "tokens": tokens,
            "context_tokens": context_tokens,
            "cost": sum(costs) if costs else None,
            "missing_metrics": sorted(missing),
        }

    case_ids: set[str] = set()
    held_out = 0
    development = 0
    regressions = 0
    critical_regressions = 0
    candidate_validator_gate = True
    for item in comparisons:
        if not isinstance(item, Mapping):
            raise PromotionError("evaluation comparison is invalid")
        case_id = _text(item.get("case_id"), "evaluation comparison case_id", limit=160)
        split = item.get("split")
        if case_id in case_ids or split not in {"development", "held-out"}:
            raise PromotionError("evaluation comparison identity is invalid")
        case_ids.add(case_id)
        held_out += split == "held-out"
        development += split == "development"
        if not isinstance(item.get("baseline_status"), str) or not isinstance(item.get("candidate_status"), str):
            raise PromotionError("evaluation comparison statuses are invalid")
        for field in ("baseline_outcome_status", "candidate_outcome_status"):
            if item.get(field) not in valid_outcomes:
                raise PromotionError("evaluation comparison outcomes are invalid")
        if not isinstance(item.get("regression"), bool) or not isinstance(item.get("critical_regression"), bool):
            raise PromotionError("evaluation comparison regression fields are invalid")
        for field in ("baseline_task_success", "candidate_task_success"):
            if not isinstance(item.get(field), bool):
                raise PromotionError("evaluation comparison task-success fields are invalid")
        baseline_validators = validate_validators(item.get("baseline_validators"), f"comparison {case_id} baseline")
        candidate_validators = validate_validators(item.get("candidate_validators"), f"comparison {case_id} candidate")
        candidate_validator_gate = candidate_validator_gate and all(item["passed"] for item in candidate_validators)
        baseline_score = item.get("baseline_score")
        candidate_score = item.get("candidate_score")
        for score in (baseline_score, candidate_score):
            if score is not None and (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or not 0.0 <= float(score) <= 1.0
            ):
                raise PromotionError("evaluation comparison score is invalid")
        expected_score_delta = None if baseline_score is None or candidate_score is None else float(candidate_score) - float(baseline_score)
        if item.get("score_delta") != expected_score_delta:
            raise PromotionError("evaluation comparison score delta is inconsistent")
        baseline_success = item["baseline_status"] == "passed" and item["baseline_outcome_status"] == "success"
        candidate_success = item["candidate_status"] == "passed" and item["candidate_outcome_status"] == "success"
        expected_regression = (
            (baseline_success and not candidate_success)
            or (item["baseline_status"] == "passed" and item["candidate_status"] != "passed")
            or (expected_score_delta is not None and expected_score_delta < 0.0)
        )
        expected_critical = (expected_regression and split == "held-out") or any(
            validator["critical"] and not validator["passed"] for validator in candidate_validators
        )
        if item["regression"] is not expected_regression or item["critical_regression"] is not expected_critical:
            raise PromotionError("evaluation comparison regression semantics are inconsistent")
        regressions += expected_regression
        critical_regressions += expected_critical

    baseline_derived = derived_metrics("baseline")
    candidate_derived = derived_metrics("candidate")
    for name, actual, expected in (
        ("baseline", baseline, baseline_derived),
        ("candidate", candidate, candidate_derived),
    ):
        for field in expected:
            value = actual.get(field)
            expected_value = expected[field]
            if isinstance(expected_value, float) and value is not None:
                if not isinstance(value, (int, float)) or not math.isclose(float(value), expected_value, rel_tol=1e-12, abs_tol=1e-12):
                    raise PromotionError(f"evaluation {name} metric {field} is inconsistent")
            elif value != expected_value:
                raise PromotionError(f"evaluation {name} metric {field} is inconsistent")

    expected_delta = {
        "task_success_rate": candidate_derived["task_success_rate"] - baseline_derived["task_success_rate"],
        "validator_score": (
            candidate_derived["validator_score"] - baseline_derived["validator_score"]
            if candidate_derived["validator_score"] is not None and baseline_derived["validator_score"] is not None
            else None
        ),
        "cost": (
            candidate_derived["cost"] - baseline_derived["cost"]
            if candidate_derived["cost"] is not None and baseline_derived["cost"] is not None
            else None
        ),
    }
    for name in ("critical_failures", "incomplete", "run_failures", "latency_ms", "tokens", "context_tokens"):
        expected_delta[name] = candidate_derived[name] - baseline_derived[name]
    for name, expected in expected_delta.items():
        actual = delta.get(name)
        if isinstance(expected, float) and actual is not None:
            if not isinstance(actual, (int, float)) or not math.isclose(float(actual), expected, rel_tol=1e-12, abs_tol=1e-12):
                raise PromotionError(f"evaluation delta {name} is inconsistent")
        elif actual != expected:
            raise PromotionError(f"evaluation delta {name} is inconsistent")

    held_out = sum(item.get("split") == "held-out" for item in comparisons)
    development = sum(item.get("split") == "development" for item in comparisons)
    for metrics in (baseline, candidate):
        for name in ("cases", "task_successes", "validator_scored_cases", "critical_failures", "incomplete", "run_failures", "latency_ms", "tokens", "context_tokens"):
            _metric_int(metrics, name)
        _metric_rate(metrics, "task_success_rate")
        _metric_rate(metrics, "validator_score")
        cost = metrics.get("cost")
        if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(float(cost)) or float(cost) < 0.0):
            raise PromotionError("evaluation cost metric is invalid")
        missing = metrics.get("missing_metrics")
        if not isinstance(missing, list) or any(not isinstance(value, str) for value in missing):
            raise PromotionError("evaluation missing metrics are invalid")
    require_held_out = config.get("require_held_out")
    if not isinstance(require_held_out, bool):
        raise PromotionError("evaluation require_held_out is invalid")
    min_evidence = _config_nonnegative(config, "min_evidence", integer=True)
    max_held_out = _config_nonnegative(config, "max_held_out_regressions", integer=True)
    max_critical = _config_nonnegative(config, "max_critical_regressions", integer=True)
    max_regressions = _config_nonnegative(config, "max_regressions", integer=True)
    if min_evidence is None or max_held_out is None or max_critical is None or max_regressions is None:
        raise PromotionError("evaluation regression configuration is incomplete")
    if candidate_cases < min_evidence or (require_held_out and (not held_out or not development)):
        raise PromotionError("evaluation minimum-evidence gate is inconsistent")
    if regressions > max_regressions or held_out > candidate_cases or critical_regressions > max_critical:
        raise PromotionError("evaluation regression metrics are inconsistent")
    missing = set(baseline.get("missing_metrics", [])) | set(candidate.get("missing_metrics", []))
    required = {"duration_ms", "tokens_used"}
    if config.get("max_context_overhead") is not None:
        required.add("context_tokens")
    if config.get("max_cost_overhead") is not None:
        required.add("cost")
    if config.get("max_candidate_cost") is not None:
        required.add("candidate.cost")
    if "candidate.cost" in required:
        required.remove("candidate.cost")
        if "cost" in set(candidate.get("missing_metrics", [])):
            raise PromotionError("evaluation candidate cost metric is missing")
    if required & missing:
        raise PromotionError("evaluation required metrics are missing")
    success_delta = delta.get("task_success_rate")
    validator_delta = delta.get("validator_score")
    if not isinstance(success_delta, (int, float)) or not isinstance(validator_delta, (int, float)) or not math.isfinite(float(success_delta)) or not math.isfinite(float(validator_delta)):
        raise PromotionError("evaluation improvement delta is missing")
    for name in ("tokens", "context_tokens"):
        value = delta.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise PromotionError(f"evaluation delta {name} is invalid")
    cost_delta = delta.get("cost")
    if cost_delta is not None and (isinstance(cost_delta, bool) or not isinstance(cost_delta, (int, float))):
        raise PromotionError("evaluation cost delta is invalid")
    min_success = _config_nonnegative(config, "min_success_delta")
    margin = _config_nonnegative(config, "non_inferiority_margin")
    if min_success is None or margin is None or float(success_delta) < min_success or float(validator_delta) < -margin:
        raise PromotionError("evaluation improvement gate is inconsistent")
    max_tokens = _config_nonnegative(config, "max_token_overhead", integer=True)
    max_context = _config_nonnegative(config, "max_context_overhead", integer=True)
    max_cost = _config_nonnegative(config, "max_cost_overhead")
    max_candidate_cost = _config_nonnegative(config, "max_candidate_cost")
    if max_tokens is not None and delta.get("tokens", 0) > max_tokens:
        raise PromotionError("evaluation token budget is inconsistent")
    if max_context is not None and delta.get("context_tokens", 0) > max_context:
        raise PromotionError("evaluation context budget is inconsistent")
    if max_cost is not None and (cost_delta is None or cost_delta > max_cost):
        raise PromotionError("evaluation cost budget is inconsistent")
    if max_candidate_cost is not None and (candidate.get("cost") is None or candidate["cost"] > max_candidate_cost):
        raise PromotionError("evaluation candidate-cost budget is inconsistent")
    gate_map = {item["name"]: item for item in gates}
    if not all(gate_map[name]["passed"] is True for name in _REQUIRED_EVALUATION_GATES):
        raise PromotionError("evaluation contains a failed gate")

    expected_gates = {
        "same_cases": baseline_cases == candidate_cases,
        "minimum_evidence": candidate_cases >= min_evidence and (not require_held_out or (held_out > 0 and development > 0)),
        "complete_runs": baseline_derived["incomplete"] == 0 and candidate_derived["incomplete"] == 0,
        "metric_completeness": not (required & missing),
        "deterministic_validators": candidate_validator_gate,
        "held_out_regressions": sum(item["regression"] and item["split"] == "held-out" for item in comparisons) <= max_held_out,
        "critical_regressions": critical_regressions <= max_critical,
        "regressions": regressions <= max_regressions,
        "improvement_or_non_inferiority": float(success_delta) >= float(min_success) and float(validator_delta) >= -float(margin),
        "cost_and_token_budget": True,
    }
    if max_tokens is not None:
        expected_gates["cost_and_token_budget"] = expected_gates["cost_and_token_budget"] and int(delta["tokens"]) <= max_tokens
    if max_context is not None:
        expected_gates["cost_and_token_budget"] = expected_gates["cost_and_token_budget"] and int(delta["context_tokens"]) <= max_context
    if max_cost is not None:
        expected_gates["cost_and_token_budget"] = expected_gates["cost_and_token_budget"] and cost_delta is not None and float(cost_delta) <= float(max_cost)
    if max_candidate_cost is not None:
        expected_gates["cost_and_token_budget"] = expected_gates["cost_and_token_budget"] and candidate.get("cost") is not None and float(candidate["cost"]) <= float(max_candidate_cost)
    for name, expected in expected_gates.items():
        if gate_map[name]["passed"] is not expected:
            raise PromotionError(f"evaluation gate {name} is inconsistent")


def _approval_path(candidate: _CandidateRecord) -> Path:
    return _require_child(candidate.path, "approval.json", "candidate approval")


def _approval_digest(value: Mapping[str, Any]) -> str:
    immutable = {key: item for key, item in value.items() if key != "approval_digest"}
    return hashlib.sha256(_canonical(immutable).encode("utf-8")).hexdigest()


def _load_approval(candidate: _CandidateRecord) -> dict[str, Any]:
    approval = _read_json(_approval_path(candidate), 32_768, "candidate approval")
    if (
        approval.get("schema_version") != PROMOTION_SCHEMA_VERSION
        or approval.get("candidate_id") != candidate.provenance.get("candidate_id")
        or approval.get("candidate_digest") != candidate.candidate_digest
        or approval.get("candidate_diff_digest") != candidate.diff_digest
        or approval.get("candidate_provenance_digest") != candidate.provenance_digest
        or approval.get("target") != candidate.provenance.get("target")
        or approval.get("parent_digest") != candidate.provenance.get("parent_digest")
        or approval.get("asset_key") != candidate.asset_key
        or approval.get("decision") not in {"approved", "rejected"}
        or approval.get("approval_digest") != _approval_digest(approval)
    ):
        raise PromotionError("candidate approval is invalid")
    _text(approval.get("operator"), "approval operator", limit=256)
    _text(approval.get("reason"), "approval reason", limit=8_192)
    _text(approval.get("evaluation_id"), "approval evaluation_id", limit=160)
    _digest(approval.get("evaluation_digest"), "approval evaluation_digest")
    _text(approval.get("decided_at"), "approval decided_at", limit=64)
    return approval


def _derive_roots(candidate_root: str | Path, active_root: str | Path | None, approved_root: str | Path | None) -> tuple[Path, Path]:
    candidate = Path(candidate_root)
    base = candidate.parent
    return Path(active_root) if active_root is not None else base / "active", Path(approved_root) if approved_root is not None else base / "approved"


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _assert_distinct_roots(candidate: Path, active: Path, approved: Path) -> None:
    if _paths_overlap(candidate, active) or _paths_overlap(candidate, approved) or _paths_overlap(active, approved):
        raise PromotionError("candidate, active, and approved roots must be separate")


def _assert_parent_current(candidate: _CandidateRecord, active_root: Path) -> None:
    parent_digest = candidate.provenance.get("parent_digest")
    target = candidate.provenance.get("target")
    if not parent_digest or not isinstance(target, str) or not target:
        return
    try:
        active_asset = SkillBank(active_root).resolve_active_target(target)
    except SkillAssetError as exc:
        raise PromotionError("active target cannot be inspected") from exc
    if active_asset is None or active_asset.digest != parent_digest:
        raise PromotionError("candidate is stale because the active parent changed")


def _approve_candidate_unlocked(
    candidate_root: str | Path,
    candidate_id: str,
    evaluation_path: str | Path,
    *,
    candidate_digest: str,
    operator: str,
    reason: str,
    decision: str = "approved",
    expected_evaluation_digest: str | None = None,
    active_root: str | Path | None = None,
    approved_root: str | Path | None = None,
) -> ApprovalResult:
    """Record an explicit human decision without activating the candidate."""

    if decision not in {"approved", "rejected"}:
        raise PromotionError("decision must be approved or rejected")
    operator = _text(operator, "operator", limit=256)
    reason = _text(reason, "reason", limit=8_192)
    candidate_digest = _digest(candidate_digest, "candidate_digest")
    candidate = _load_candidate(candidate_root, candidate_id)
    if candidate.candidate_digest != candidate_digest:
        raise PromotionError("candidate digest does not match the candidate asset")
    evaluation, evaluation_digest = _load_evaluation(evaluation_path, candidate, expected_evaluation_digest)
    evaluation_id = str(evaluation["id"])
    if candidate.status == "awaiting_approval":
        existing = _load_approval(candidate)
        if (
            existing.get("decision") == decision
            and existing.get("operator") == operator
            and existing.get("reason") == reason
            and existing.get("evaluation_id") == evaluation_id
            and existing.get("evaluation_digest") == evaluation_digest
        ):
            return ApprovalResult(candidate_id, candidate_digest, evaluation_id, evaluation_digest, decision, candidate.status, _approval_path(candidate), str(existing["approval_digest"]), str(existing["decided_at"]))
        raise PromotionError("candidate already has a different approval decision")
    if candidate.status != "tested":
        raise PromotionError("candidate must be tested before approval")
    existing = _load_approval(candidate) if _approval_path(candidate).exists() else None
    if existing is not None and not (
        existing.get("decision") == decision
        and existing.get("operator") == operator
        and existing.get("reason") == reason
        and existing.get("evaluation_id") == evaluation_id
        and existing.get("evaluation_digest") == evaluation_digest
    ):
        raise PromotionError("candidate already has a different approval record")
    active, approved = _derive_roots(candidate.root, active_root, approved_root)
    _assert_distinct_roots(candidate.root, _safe_optional_root(active, "active root"), _safe_optional_root(approved, "approved root"))
    if decision == "approved":
        _assert_parent_current(candidate, _safe_optional_root(active, "active root"))
    transition_status = "awaiting_approval" if decision == "approved" else "rejected"
    decided_at = str(existing["decided_at"]) if existing is not None else _now()
    approval = existing or {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "candidate_digest": candidate_digest,
        "candidate_diff_digest": candidate.diff_digest,
        "candidate_provenance_digest": candidate.provenance_digest,
        "target": candidate.provenance.get("target"),
        "parent_digest": candidate.provenance.get("parent_digest"),
        "asset_key": candidate.asset_key,
        "evaluation_id": evaluation_id,
        "evaluation_digest": evaluation_digest,
        "decision": decision,
        "operator": operator,
        "reason": reason,
        "decided_at": decided_at,
    }
    if existing is None:
        approval["approval_digest"] = _approval_digest(approval)
    candidate_provenance = candidate.path / "provenance.json"
    approval_path = _approval_path(candidate)
    snapshot = _snapshot([candidate_provenance, approval_path])
    try:
        _write_json(approval_path, approval)
        _transition_candidate_unlocked(
            candidate.path,
            candidate_id,
            transition_status,
            expected_status=candidate.status,
            expected_candidate_digest=candidate.candidate_digest,
            expected_parent_digest=candidate.provenance.get("parent_digest"),
            expected_diff_digest=candidate.diff_digest,
            expected_provenance_digest=candidate.provenance_digest,
            active_root=active,
            approved_root=approved,
        )
    except Exception as exc:
        try:
            _restore_snapshot(snapshot)
        except Exception as restore_exc:
            raise PromotionError("approval failed and candidate state could not be restored") from restore_exc
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError("approval failed; candidate state was restored") from exc
    return ApprovalResult(candidate_id, candidate_digest, evaluation_id, evaluation_digest, decision, transition_status, _approval_path(candidate), approval["approval_digest"], decided_at)


def approve_candidate(
    candidate_root: str | Path,
    candidate_id: str,
    evaluation_path: str | Path,
    *,
    candidate_digest: str,
    operator: str,
    reason: str,
    decision: str = "approved",
    expected_evaluation_digest: str | None = None,
    active_root: str | Path | None = None,
    approved_root: str | Path | None = None,
) -> ApprovalResult:
    """Record an explicit decision while serializing candidate state changes."""

    if not _SAFE_CANDIDATE_ID.fullmatch(candidate_id):
        raise PromotionError("candidate_id is unsafe")
    root = _safe_root(candidate_root, "candidate root")
    with _candidate_lock(root / candidate_id):
        return _approve_candidate_unlocked(
            root,
            candidate_id,
            evaluation_path,
            candidate_digest=candidate_digest,
            operator=operator,
            reason=reason,
            decision=decision,
            expected_evaluation_digest=expected_evaluation_digest,
            active_root=active_root,
            approved_root=approved_root,
        )


def _git(repo_root: Path, arguments: list[str], *, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PromotionError("Git is unavailable or timed out") from exc
    if result.returncode != 0 and not allow_failure:
        detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:500]
        raise PromotionError(f"git {' '.join(arguments[:2])} failed: {detail}")
    return result


def _git_root(repo_root: str | Path) -> Path:
    requested = _safe_root(repo_root, "repository root")
    result = _git(requested, ["rev-parse", "--show-toplevel"])
    actual = Path(result.stdout.strip()).resolve()
    if actual != requested:
        raise PromotionError("repository root must be the Git worktree root")
    return actual


def _relative_git_path(repo_root: Path, path: Path) -> str:
    try:
        resolved = path.resolve()
        relative = resolved.relative_to(repo_root)
    except (OSError, ValueError) as exc:
        raise PromotionError("promotion path is outside the Git repository") from exc
    return relative.as_posix()


def _git_paths(repo_root: Path, paths: list[Path]) -> list[str]:
    return [_relative_git_path(repo_root, path) for path in paths]


def _assert_git_targets_clean(repo_root: Path, paths: list[Path]) -> None:
    relative = _git_paths(repo_root, paths)
    status = _git(repo_root, ["status", "--porcelain=v1", "--untracked-files=all", "--", *relative]).stdout.strip()
    staged = _git(repo_root, ["diff", "--cached", "--name-only", "--", *relative]).stdout.strip()
    if status or staged:
        raise PromotionError("Git target state is dirty")


def _reset_paths(repo_root: Path, paths: list[Path]) -> None:
    relative = _git_paths(repo_root, paths)
    result = _git(repo_root, ["reset", "--quiet", "--", *relative], allow_failure=True)
    if result.returncode != 0:
        raise PromotionError("Git index cleanup failed after promotion error")


def _force_reset_paths(repo_root: Path, paths: list[Path]) -> None:
    relative = _git_paths(repo_root, paths)
    result = _git(repo_root, ["reset", "--mixed", "HEAD", "--", *relative], allow_failure=True)
    if result.returncode != 0:
        raise PromotionError("Git fallback index cleanup failed after promotion error")


def _commit_paths(repo_root: Path, paths: list[Path], message: str) -> str:
    relative = _git_paths(repo_root, paths)
    _git(repo_root, ["add", "--", *relative])
    _git(repo_root, ["diff", "--cached", "--check", "--", *relative])
    _git(repo_root, ["commit", "--only", "-m", message, "--", *relative])
    try:
        return _git(repo_root, ["rev-parse", "HEAD"]).stdout.strip()
    except PromotionError:
        return "unknown"


def _snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
    snapshot: dict[Path, bytes | None] = {}
    for path in paths:
        if _is_link(path):
            raise PromotionError("promotion target is a symlink or junction")
        if path.exists():
            if not path.is_file():
                raise PromotionError("promotion target is not a file")
            try:
                snapshot[path] = path.read_bytes()
            except OSError as exc:
                raise PromotionError("promotion target cannot be read") from exc
        else:
            snapshot[path] = None
    return snapshot


def _restore_snapshot(snapshot: Mapping[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        try:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write_bytes(path, content)
        except OSError as exc:
            raise PromotionError("promotion rollback could not restore the prior files") from exc


def _cleanup_failed_mutation(
    repo_root: Path,
    commit_paths: list[Path],
    snapshot: Mapping[Path, bytes | None],
    remove_directory: Path | None = None,
) -> None:
    errors: list[Exception] = []
    try:
        _reset_paths(repo_root, commit_paths)
    except Exception as exc:
        try:
            _force_reset_paths(repo_root, commit_paths)
        except Exception as fallback_exc:
            errors.extend((exc, fallback_exc))
    try:
        _restore_snapshot(snapshot)
    except Exception as exc:
        errors.append(exc)
    if remove_directory is not None and remove_directory.exists():
        try:
            if _is_link(remove_directory):
                raise PromotionError("promotion rollback target became a symlink")
            shutil.rmtree(remove_directory)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise PromotionError("promotion failed and could not be rolled back safely") from errors[0]


def _load_manifest(approved_root: Path) -> dict[str, Any]:
    path = approved_root / "manifest.json"
    if not path.exists():
        return {"schema_version": PROMOTION_SCHEMA_VERSION, "revision": 0, "active": {}, "history": []}
    manifest = _read_json(path, MAX_MANIFEST_BYTES, "approved manifest")
    if (
        manifest.get("schema_version") != PROMOTION_SCHEMA_VERSION
        or isinstance(manifest.get("revision"), bool)
        or not isinstance(manifest.get("revision"), int)
        or manifest["revision"] < 0
        or not isinstance(manifest.get("active"), dict)
        or not isinstance(manifest.get("history"), list)
        or len(manifest["history"]) > 10_000
    ):
        raise PromotionError("approved manifest is invalid")
    for asset_key, entry in manifest["active"].items():
        _validate_manifest_entry(approved_root, asset_key, entry)
    return manifest


def _validate_manifest_entry(approved_root: Path, asset_key: str, entry: Any) -> None:
    depth = 0
    current = entry
    while True:
        if depth > 10_000:
            raise PromotionError("approved manifest history is too deep")
        if not isinstance(current, Mapping):
            raise PromotionError("approved manifest entry is invalid")
        candidate_id = current.get("candidate_id")
        if not isinstance(candidate_id, str) or not _SAFE_CANDIDATE_ID.fullmatch(candidate_id):
            raise PromotionError("approved manifest candidate ID is invalid")
        candidate_digest = _digest(current.get("candidate_digest"), "approved manifest candidate_digest")
        if current.get("asset_key") != asset_key:
            raise PromotionError("approved manifest asset key is inconsistent")
        expected_asset_path = f"{candidate_id}/SKILL.md"
        expected_provenance_path = f"{candidate_id}/provenance.json"
        if current.get("asset_path") != expected_asset_path or current.get("provenance_path") != expected_provenance_path:
            raise PromotionError("approved manifest asset paths are invalid")
        _text(current.get("evaluation_id"), "approved manifest evaluation_id", limit=160)
        _digest(current.get("evaluation_digest"), "approved manifest evaluation_digest")
        manifest_candidate_provenance_digest = _digest(
            current.get("candidate_provenance_digest"),
            "approved manifest candidate_provenance_digest",
        )
        manifest_approved_provenance_digest = _digest(
            current.get("approved_provenance_digest"),
            "approved manifest approved_provenance_digest",
        )
        asset_dir = _require_child(approved_root, candidate_id, "approved candidate")
        skill_path = _require_child(asset_dir, "SKILL.md", "approved skill")
        provenance_path = _require_child(asset_dir, "provenance.json", "approved provenance")
        try:
            if not asset_dir.is_dir() or _is_link(asset_dir):
                raise PromotionError("approved candidate directory is unavailable")
            skill = read_bounded(skill_path, MAX_CANDIDATE_BODY_BYTES)
        except (OSError, SkillAssetError) as exc:
            raise PromotionError("approved candidate asset is unavailable") from exc
        if content_digest(skill) != candidate_digest:
            raise PromotionError("approved manifest candidate digest does not match the asset")
        provenance = _read_json(provenance_path, MAX_PROVENANCE_BYTES, "approved provenance")
        if (
            provenance.get("status") != "approved"
            or provenance.get("candidate_id") != candidate_id
            or provenance.get("candidate_digest") != candidate_digest
            or provenance.get("provenance_digest") != manifest_approved_provenance_digest
            or provenance.get("candidate_provenance_digest") != manifest_candidate_provenance_digest
            or provenance_digest(provenance) != provenance.get("provenance_digest")
        ):
            raise PromotionError("approved provenance does not match the manifest")
        previous = current.get("previous")
        if previous is None:
            return
        if not isinstance(previous, Mapping):
            raise PromotionError("approved manifest previous entry is invalid")
        current = previous
        depth += 1


def _manifest_entry(candidate: _CandidateRecord, evaluation: Mapping[str, Any], approval: Mapping[str, Any], approved_at: str, previous: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "candidate_id": candidate.provenance["candidate_id"],
        "candidate_digest": candidate.candidate_digest,
        "candidate_provenance_digest": candidate.provenance_digest,
        "approved_provenance_digest": "",  # filled after the approved sidecar is derived
        "evaluation_id": evaluation["id"],
        "evaluation_digest": approval["evaluation_digest"],
        "asset_key": candidate.asset_key,
        "asset_path": f"{candidate.provenance['candidate_id']}/SKILL.md",
        "provenance_path": f"{candidate.provenance['candidate_id']}/provenance.json",
        "approved_at": approved_at,
        "operator": approval["operator"],
        "reason": approval["reason"],
        "previous": dict(previous) if previous is not None else None,
    }


def _promoted_provenance(candidate: _CandidateRecord, evaluation: Mapping[str, Any], approval: Mapping[str, Any], entry: Mapping[str, Any], approved_at: str) -> dict[str, Any]:
    provenance = dict(candidate.provenance)
    provenance.update(
        {
            "status": "approved",
            "evaluation_id": evaluation["id"],
            "evaluation_digest": approval["evaluation_digest"],
            "approval": dict(approval),
            "asset_key": entry["asset_key"],
            "candidate_provenance_digest": candidate.provenance_digest,
            "approved_at": approved_at,
        }
    )
    provenance["provenance_digest"] = provenance_digest(provenance)
    return provenance


def _promote_candidate_unlocked(
    candidate_root: str | Path,
    candidate_id: str,
    evaluation_path: str | Path,
    approved_root: str | Path,
    repo_root: str | Path,
    *,
    expected_approval_digest: str,
    expected_candidate_digest: str | None = None,
    active_root: str | Path | None = None,
) -> PromotionResult:
    """Promote an explicitly approved candidate in one local Git commit."""

    candidate = _load_candidate(candidate_root, candidate_id)
    if candidate.status != "awaiting_approval":
        raise PromotionError("candidate must have an approved awaiting_approval decision")
    approval = _load_approval(candidate)
    if approval["decision"] != "approved":
        raise PromotionError("candidate was not approved")
    if _digest(expected_approval_digest, "expected_approval_digest") != approval["approval_digest"]:
        raise PromotionError("approval digest confirmation does not match the approval record")
    if expected_candidate_digest is not None and _digest(expected_candidate_digest, "expected_candidate_digest") != candidate.candidate_digest:
        raise PromotionError("candidate digest confirmation does not match the candidate")
    evaluation, evaluation_digest = _load_evaluation(evaluation_path, candidate, str(approval["evaluation_digest"]))
    if evaluation["id"] != approval["evaluation_id"] or evaluation_digest != approval["evaluation_digest"]:
        raise PromotionError("evaluation does not match the approval decision")
    repo = _git_root(repo_root)
    candidate_root_path = _safe_root(candidate_root, "candidate root")
    approved = _safe_root(approved_root, "approved root", create=True)
    active, _ = _derive_roots(candidate_root_path, active_root, approved)
    active_path = _safe_optional_root(active, "active root")
    _assert_distinct_roots(candidate_root_path, active_path, approved)
    for root, field in ((candidate_root_path, "candidate root"), (approved, "approved root"), (active_path, "active root")):
        try:
            root.relative_to(repo)
        except ValueError as exc:
            raise PromotionError(f"{field} must be inside the Git repository") from exc
    approved_dir = approved / candidate_id
    if _is_link(approved_dir) or approved_dir.exists():
        raise PromotionError("approved candidate path already exists")
    manifest_path = approved / "manifest.json"
    approved_skill = approved_dir / "SKILL.md"
    approved_provenance = approved_dir / "provenance.json"
    candidate_provenance = candidate.path / "provenance.json"
    approval_path = _approval_path(candidate)
    target_paths = [manifest_path, approved_skill, approved_provenance]
    _assert_git_targets_clean(repo, target_paths)
    commit_paths = [candidate_provenance, approval_path, approved_skill, approved_provenance, manifest_path]
    _git_paths(repo, commit_paths)
    manifest = _load_manifest(approved)
    previous = manifest["active"].get(candidate.asset_key)
    if previous is not None and not isinstance(previous, Mapping):
        raise PromotionError("approved manifest contains an invalid previous entry")
    approved_at = _now()
    entry = _manifest_entry(candidate, evaluation, approval, approved_at, previous)
    approved_provenance_value = _promoted_provenance(candidate, evaluation, approval, entry, approved_at)
    entry["approved_provenance_digest"] = approved_provenance_value["provenance_digest"]
    next_manifest = dict(manifest)
    next_manifest["revision"] = manifest["revision"] + 1
    next_manifest["updated_at"] = approved_at
    next_manifest["active"] = dict(manifest["active"])
    next_manifest["active"][candidate.asset_key] = entry
    history_entry = {key: value for key, value in entry.items() if key != "previous"}
    if isinstance(previous, Mapping):
        history_entry["previous_candidate_id"] = previous.get("candidate_id")
        history_entry["previous_digest"] = previous.get("candidate_digest")
    next_manifest["history"] = [*manifest["history"], {"action": "promote", **history_entry}]
    snapshot = _snapshot([candidate_provenance, approved_skill, approved_provenance, manifest_path])
    created_dir = False
    try:
        approved_dir.mkdir(parents=False)
        created_dir = True
        _atomic_write_bytes(approved_skill, candidate.skill)
        _write_json(approved_provenance, approved_provenance_value)
        _write_json(manifest_path, next_manifest)
        _transition_candidate_unlocked(
            candidate.path,
            candidate_id,
            "approved",
            expected_status=candidate.status,
            expected_candidate_digest=candidate.candidate_digest,
            expected_parent_digest=candidate.provenance.get("parent_digest"),
            expected_diff_digest=candidate.diff_digest,
            expected_provenance_digest=candidate.provenance_digest,
            active_root=active_path,
            approved_root=approved,
        )
        if hashlib.sha256(approved_skill.read_bytes()).hexdigest() != candidate.candidate_digest:
            raise PromotionError("approved skill failed post-write digest validation")
        stored_approved = _read_json(approved_provenance, MAX_PROVENANCE_BYTES, "approved provenance")
        if stored_approved.get("status") != "approved" or stored_approved.get("candidate_digest") != candidate.candidate_digest:
            raise PromotionError("approved provenance failed post-write validation")
        final_candidate = _load_candidate(candidate.root, candidate_id)
        final_approval = _load_approval(final_candidate)
        if final_candidate.status != "approved" or final_approval["approval_digest"] != approval["approval_digest"]:
            raise PromotionError("candidate approval changed during promotion")
        final_evaluation, final_evaluation_digest = _load_evaluation(
            evaluation_path,
            final_candidate,
            str(approval["evaluation_digest"]),
        )
        if final_evaluation["id"] != evaluation["id"] or final_evaluation_digest != evaluation_digest:
            raise PromotionError("evaluation changed during promotion")
        commit = _commit_paths(repo, commit_paths, f"axel: promote {candidate_id} evaluation {evaluation['id']}")
    except Exception as exc:
        try:
            _cleanup_failed_mutation(repo, commit_paths, snapshot, approved_dir if created_dir else None)
        except Exception as restore_exc:
            raise PromotionError("promotion failed and could not be rolled back safely") from restore_exc
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError("promotion failed; prior files were restored") from exc
    return PromotionResult(
        candidate_id,
        candidate.candidate_digest,
        str(evaluation["id"]),
        candidate.asset_key,
        approved_dir,
        manifest_path,
        commit,
        str(previous["candidate_id"]) if isinstance(previous, Mapping) else None,
    )


def promote_candidate(
    candidate_root: str | Path,
    candidate_id: str,
    evaluation_path: str | Path,
    approved_root: str | Path,
    repo_root: str | Path,
    *,
    expected_approval_digest: str,
    expected_candidate_digest: str | None = None,
    active_root: str | Path | None = None,
) -> PromotionResult:
    """Promote a candidate while serializing approved-manifest updates."""

    if not _SAFE_CANDIDATE_ID.fullmatch(candidate_id):
        raise PromotionError("candidate_id is unsafe")
    candidate_root_path = _safe_root(candidate_root, "candidate root")
    approved = _safe_root(approved_root, "approved root", create=True)
    with _manifest_lock(approved):
        with _candidate_lock(candidate_root_path / candidate_id):
            return _promote_candidate_unlocked(
                candidate_root_path,
                candidate_id,
                evaluation_path,
                approved,
                repo_root,
                expected_approval_digest=expected_approval_digest,
                expected_candidate_digest=expected_candidate_digest,
                active_root=active_root,
            )


def _rollback_candidate_unlocked(
    candidate_root: str | Path,
    candidate_id: str,
    approved_root: str | Path,
    repo_root: str | Path,
    *,
    operator: str,
    reason: str,
    active_root: str | Path | None = None,
) -> RollbackResult:
    """Restore the previous approved manifest entry by candidate ID."""

    operator = _text(operator, "operator", limit=256)
    reason = _text(reason, "reason", limit=8_192)
    candidate = _load_candidate(candidate_root, candidate_id)
    if candidate.status != "approved":
        raise PromotionError("candidate must be approved before rollback")
    approval = _load_approval(candidate)
    if approval["decision"] != "approved":
        raise PromotionError("candidate does not have an approval decision")
    repo = _git_root(repo_root)
    candidate_root_path = _safe_root(candidate_root, "candidate root")
    approved = _safe_root(approved_root, "approved root")
    active, _ = _derive_roots(candidate_root_path, active_root, approved)
    active_path = _safe_optional_root(active, "active root")
    _assert_distinct_roots(candidate_root_path, active_path, approved)
    for root, field in ((candidate_root_path, "candidate root"), (approved, "approved root"), (active_path, "active root")):
        try:
            root.relative_to(repo)
        except ValueError as exc:
            raise PromotionError(f"{field} must be inside the Git repository") from exc
    manifest_path = approved / "manifest.json"
    manifest = _load_manifest(approved)
    current = manifest["active"].get(candidate.asset_key)
    if not isinstance(current, Mapping) or current.get("candidate_id") != candidate_id or current.get("candidate_digest") != candidate.candidate_digest:
        raise PromotionError("candidate is not the active approved version")
    previous = current.get("previous")
    if previous is not None and not isinstance(previous, Mapping):
        raise PromotionError("approved manifest previous entry is invalid")
    if previous is not None:
        _validate_manifest_entry(approved, candidate.asset_key, previous)
    target_paths = [manifest_path]
    for entry in (current, previous):
        if isinstance(entry, Mapping):
            entry_dir = approved / str(entry["candidate_id"])
            target_paths.extend((entry_dir / "SKILL.md", entry_dir / "provenance.json"))
    _assert_git_targets_clean(repo, target_paths)
    commit_paths = [candidate.path / "provenance.json", manifest_path]
    _git_paths(repo, commit_paths)
    rolled_back_at = _now()
    next_manifest = dict(manifest)
    next_manifest["revision"] = manifest["revision"] + 1
    next_manifest["updated_at"] = rolled_back_at
    next_manifest["active"] = dict(manifest["active"])
    if previous is None:
        next_manifest["active"].pop(candidate.asset_key, None)
    else:
        next_manifest["active"][candidate.asset_key] = dict(previous)
    next_manifest["history"] = [
        *manifest["history"],
        {
            "action": "rollback",
            "candidate_id": candidate_id,
            "candidate_digest": candidate.candidate_digest,
            "asset_key": candidate.asset_key,
            "operator": operator,
            "reason": reason,
            "rolled_back_at": rolled_back_at,
            "restored_candidate_id": previous.get("candidate_id") if isinstance(previous, Mapping) else None,
            "restored_digest": previous.get("candidate_digest") if isinstance(previous, Mapping) else None,
        },
    ]
    snapshot = _snapshot([candidate.path / "provenance.json", manifest_path])
    try:
        _transition_candidate_unlocked(
            candidate.path,
            candidate_id,
            "rolled_back",
            expected_status=candidate.status,
            expected_candidate_digest=candidate.candidate_digest,
            expected_parent_digest=candidate.provenance.get("parent_digest"),
            expected_diff_digest=candidate.diff_digest,
            expected_provenance_digest=candidate.provenance_digest,
            active_root=active_path,
            approved_root=approved,
        )
        _write_json(manifest_path, next_manifest)
        commit = _commit_paths(repo, commit_paths, f"axel: rollback {candidate_id}")
    except Exception as exc:
        try:
            _cleanup_failed_mutation(repo, commit_paths, snapshot)
        except Exception as restore_exc:
            raise PromotionError("rollback failed and could not restore the prior files") from restore_exc
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError("rollback failed; prior files were restored") from exc
    return RollbackResult(
        candidate_id,
        candidate.candidate_digest,
        candidate.asset_key,
        previous.get("candidate_id") if isinstance(previous, Mapping) else None,
        previous.get("candidate_digest") if isinstance(previous, Mapping) else None,
        manifest_path,
        commit,
    )


def rollback_candidate(
    candidate_root: str | Path,
    candidate_id: str,
    approved_root: str | Path,
    repo_root: str | Path,
    *,
    operator: str,
    reason: str,
    active_root: str | Path | None = None,
) -> RollbackResult:
    """Rollback a candidate while serializing approved-manifest updates."""

    if not _SAFE_CANDIDATE_ID.fullmatch(candidate_id):
        raise PromotionError("candidate_id is unsafe")
    candidate_root_path = _safe_root(candidate_root, "candidate root")
    approved = _safe_root(approved_root, "approved root")
    with _manifest_lock(approved):
        with _candidate_lock(candidate_root_path / candidate_id):
            return _rollback_candidate_unlocked(
                candidate_root_path,
                candidate_id,
                approved,
                repo_root,
                operator=operator,
                reason=reason,
                active_root=active_root,
            )

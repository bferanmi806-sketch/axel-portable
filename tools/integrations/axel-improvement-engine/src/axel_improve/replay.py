"""Sanitized replay suites and a deterministic local fixture runner."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .errors import RecordValidationError, UnsafePathError
from .models import Trajectory
from .redaction import RedactionConfig, Redactor, reject_unsafe_paths
from .skills import SkillAssetError, _is_link, _reject_link_ancestors, read_bounded
from .store import atomic_write_text


REPLAY_SCHEMA_VERSION = 1
DEVELOPMENT_SPLIT = "development"
HELD_OUT_SPLIT = "held-out"
VALID_SPLITS = frozenset({DEVELOPMENT_SPLIT, HELD_OUT_SPLIT})
DEFAULT_RUNNER = "deterministic-fixture-v1"
DEFAULT_TIMEOUT_MS = 1_000
DEFAULT_PROCESS_BUDGET = 1
DEFAULT_TOKEN_BUDGET = 4_096
MAX_TIMEOUT_MS = 300_000
MAX_PROCESS_BUDGET = 100
MAX_TOKEN_BUDGET = 1_000_000
MAX_CASES = 10_000
MAX_FIXTURE_EVENTS = 100
MAX_FIXTURE_BYTES = 262_144
MAX_SUITE_BYTES = 16 * 1024 * 1024
_CASE_ID = re.compile(r"replay-[A-Za-z0-9][A-Za-z0-9-]{7,63}$")
_SAFE_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SECRET_LIKE_ID = re.compile(
    r"(?i)(?:sk|gh[pousr]?|github_pat|xox[baprs]|AIza)[_-][A-Za-z0-9._-]{8,}|AKIA[0-9A-Z]{16}"
)
_VALID_EVENT_TYPES = frozenset(
    {"user_prompt", "assistant_message", "tool_call", "tool_result", "file_change", "validator"}
)
_VALID_OUTCOME_STATUSES = frozenset({"success", "failure", "partial", "cancelled", "unevaluated"})


class ReplayError(ValueError):
    """Raised when a replay suite or runner configuration is unsafe."""


def _strict_redactor() -> Redactor:
    """Use environment secrets without truncating valid suite-sized collections."""

    config = RedactionConfig(
        max_string_chars=MAX_SUITE_BYTES,
        max_output_chars=MAX_SUITE_BYTES,
        max_collection_items=MAX_CASES,
        max_depth=32,
    )
    return Redactor.from_environment(config)


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ReplayError("replay data must be JSON-compatible") from exc


def _text(value: Any, field: str, *, required: bool = True, limit: int = 8_192) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ReplayError(f"{field} must be a non-empty string")
    text = value.strip()
    if "\x00" in text:
        raise ReplayError(f"{field} contains a NUL byte")
    if len(text.encode("utf-8")) > limit:
        raise ReplayError(f"{field} exceeds size limit")
    return text


def _optional_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReplayError(f"{field} must be a number or null")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ReplayError(f"{field} must be between 0 and 1")
    return result


def _nonnegative_int(value: Any, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise ReplayError(f"{field} must be an integer from 0 to {maximum}")
    return value


def _normalise_evidence(value: Any, field: str = "evidence") -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReplayError(f"{field} must be an object")
    outcome_status = _text(value.get("outcome_status"), f"{field}.outcome_status", limit=32)
    if outcome_status not in _VALID_OUTCOME_STATUSES:
        raise ReplayError(f"{field}.outcome_status is unsupported")
    outcome_summary = _text(value.get("outcome_summary"), f"{field}.outcome_summary", limit=8_192)
    evaluation_status = _text(value.get("evaluation_status"), f"{field}.evaluation_status", limit=32)
    if evaluation_status not in {"evaluated", "unevaluated"}:
        raise ReplayError(f"{field}.evaluation_status is unsupported")
    validators_raw = value.get("validators", [])
    if not isinstance(validators_raw, list) or len(validators_raw) > MAX_FIXTURE_EVENTS:
        raise ReplayError(f"{field}.validators must be a bounded list")
    validators: list[dict[str, Any]] = []
    for item in validators_raw:
        if not isinstance(item, Mapping):
            raise ReplayError(f"{field}.validators must contain objects")
        name = _text(item.get("name"), f"{field}.validator.name", limit=160)
        passed = item.get("passed")
        if not isinstance(passed, bool):
            raise ReplayError(f"{field}.validator.passed must be boolean")
        details = _text(item.get("details"), f"{field}.validator.details", required=False, limit=2_048)
        validators.append(
            {
                "name": name,
                "passed": passed,
                "score": _optional_number(item.get("score"), f"{field}.validator.score"),
                "details": details,
            }
        )
    result = {
        "outcome_status": outcome_status,
        "outcome_summary": outcome_summary,
        "evaluation_status": evaluation_status,
        "evaluator": _text(value.get("evaluator"), f"{field}.evaluator", required=False, limit=160),
        "score": _optional_number(value.get("score"), f"{field}.score"),
        "validators": validators,
        "fixture_digest": _text(value.get("fixture_digest"), f"{field}.fixture_digest", required=False, limit=64),
    }
    if result["fixture_digest"] is not None and not re.fullmatch(r"[0-9a-f]{64}", result["fixture_digest"]):
        raise ReplayError(f"{field}.fixture_digest is invalid")
    if evaluation_status == "evaluated" and result["evaluator"] is None and not validators:
        raise ReplayError(f"{field} has no evaluator or validator evidence")
    if len(_canonical(result).encode("utf-8")) > MAX_FIXTURE_BYTES:
        raise ReplayError(f"{field} exceeds size limit")
    return result


@dataclass(frozen=True)
class RunnerConfig:
    """Bounded settings for the local deterministic fixture runner."""

    runner: str = DEFAULT_RUNNER
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    process_budget: int = DEFAULT_PROCESS_BUDGET
    token_budget: int = DEFAULT_TOKEN_BUDGET
    network: str = "disabled"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "RunnerConfig":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ReplayError("runner_config must be an object")
        runner = _text(value.get("runner", DEFAULT_RUNNER), "runner_config.runner", limit=80)
        if runner != DEFAULT_RUNNER:
            raise ReplayError("unsupported replay runner")
        network = _text(value.get("network", "disabled"), "runner_config.network", limit=32)
        if network != "disabled":
            raise ReplayError("replay network access must remain disabled")
        return cls(
            runner=runner,
            timeout_ms=_nonnegative_int(value.get("timeout_ms", DEFAULT_TIMEOUT_MS), "runner_config.timeout_ms", MAX_TIMEOUT_MS),
            process_budget=_nonnegative_int(value.get("process_budget", DEFAULT_PROCESS_BUDGET), "runner_config.process_budget", MAX_PROCESS_BUDGET),
            token_budget=_nonnegative_int(value.get("token_budget", DEFAULT_TOKEN_BUDGET), "runner_config.token_budget", MAX_TOKEN_BUDGET),
            network=network,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runner": self.runner,
            "timeout_ms": self.timeout_ms,
            "process_budget": self.process_budget,
            "token_budget": self.token_budget,
            "network": self.network,
        }


def _normalise_fixture(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_FIXTURE_EVENTS:
        raise ReplayError("fixture must be a non-empty bounded list")
    events: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ReplayError("fixture events must be objects")
        event_id = _text(item.get("id"), "fixture.event.id", limit=160)
        event_type = _text(item.get("type"), "fixture.event.type", limit=80)
        if event_id is None or event_id in event_ids:
            raise ReplayError("fixture event IDs must be non-empty and unique")
        if event_type not in _VALID_EVENT_TYPES:
            raise ReplayError("fixture event type is unsupported")
        event_ids.add(event_id)
        event = {
            "id": event_id,
            "type": event_type,
            "tool_name": _text(item.get("tool_name"), "fixture.event.tool_name", required=False, limit=160),
            "input": item.get("input"),
            "output": item.get("output"),
            "status": _text(item.get("status"), "fixture.event.status", required=False, limit=80),
            "duration_ms": _nonnegative_int(item.get("duration_ms", 0), "fixture.event.duration_ms", 86_400_000),
        }
        _canonical(event)
        events.append(event)
    if len(_canonical(events).encode("utf-8")) > MAX_FIXTURE_BYTES:
        raise ReplayError("fixture exceeds size limit")
    return tuple(events)


def _case_id(source_trajectory_id: str, project_reference: str) -> str:
    digest = hashlib.sha256(f"{project_reference}\0{source_trajectory_id}".encode("utf-8")).hexdigest()[:24]
    return f"replay-{digest}"


def _case_group(task_input: str) -> str:
    digest = hashlib.sha256(task_input.casefold().strip().encode("utf-8")).hexdigest()
    return f"group-{digest}"


def _session_group(project_reference: str, session_reference: str) -> str:
    digest = hashlib.sha256(f"{project_reference}\0{session_reference}".encode("utf-8")).hexdigest()
    return f"session-{digest}"


def _contains_marker(value: Any, marker: str) -> bool:
    if isinstance(value, str):
        return marker in value
    if isinstance(value, Mapping):
        return any(_contains_marker(key, marker) or _contains_marker(child, marker) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_marker(child, marker) for child in value)
    return False


def _safe_source_label(value: Any, index: int, redactor: Redactor | None = None) -> str:
    if isinstance(value, str) and value.strip() and _SAFE_SOURCE_ID.fullmatch(value.strip()):
        if _SECRET_LIKE_ID.search(value.strip()):
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
            return f"input-{digest}"
        sanitized = (redactor or _strict_redactor()).fork().sanitize(value.strip())
        if sanitized == value.strip():
            return value.strip()
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        return f"input-{digest}"
    if isinstance(value, str) and value.strip():
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        return f"input-{digest}"
    return f"input-{index}"


def _asset_version(trajectory: Trajectory) -> str:
    metadata = trajectory.metadata or {}
    for key in ("asset_version", "asset_digest", "skill_version"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip() and not _contains_marker(value, "[REDACTED:"):
            return value.strip()[:512]
    return "baseline"


def _record_redaction_stat(record: Mapping[str, Any], name: str) -> int:
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return 0
    redaction = metadata.get("redaction")
    if not isinstance(redaction, Mapping):
        return 0
    value = redaction.get(name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _event_dict(event: Any) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.event_type,
        "tool_name": event.tool_name,
        "input": event.input,
        "output": event.output,
        "status": event.status,
        "duration_ms": event.duration_ms or 0,
    }


def _fixture_digest(task_input: str, fixture: Iterable[Mapping[str, Any]], asset_version: str) -> str:
    payload = {
        "task_input": task_input,
        "fixture": list(fixture),
        "asset_version": asset_version,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _trajectory_evidence(trajectory: Trajectory) -> dict[str, Any]:
    return _normalise_evidence(
        {
            "outcome_status": trajectory.outcome_status,
            "outcome_summary": trajectory.outcome_summary,
            "evaluation_status": trajectory.evaluation.status,
            "evaluator": trajectory.evaluation.evaluator,
            "score": trajectory.evaluation.score,
            "validators": [validator.to_dict() for validator in trajectory.evaluation.validators],
        },
        "trajectory evidence",
    )


@dataclass(frozen=True)
class ReplayCase:
    """One sanitized historical case assigned to a replay split."""

    id: str
    source_trajectory_id: str
    task_input: str
    fixture_reference: str
    project_reference: str
    session_reference: str
    asset_version: str
    expected_evidence: dict[str, Any]
    fixture_result: dict[str, Any]
    fixture: tuple[dict[str, Any], ...]
    runner_config: dict[str, Any]
    split: str
    case_group: str
    schema_version: int = REPLAY_SCHEMA_VERSION

    @classmethod
    def from_trajectory(
        cls,
        trajectory: Trajectory,
        *,
        split: str,
        runner_config: RunnerConfig,
        source_id: str | None = None,
    ) -> "ReplayCase":
        if split not in VALID_SPLITS:
            raise ReplayError("unsupported replay split")
        source_id = _text(
            _safe_source_label(source_id or trajectory.id, 0, _strict_redactor()),
            "source_trajectory_id",
            limit=160,
        )
        task_input = _text(trajectory.task, "task_input", limit=16_384)
        project_reference = _text(trajectory.project_id, "project_reference", limit=512)
        session_reference = _text(trajectory.session_id, "session_reference", limit=512)
        if source_id is None or task_input is None or project_reference is None or session_reference is None:
            raise ReplayError("trajectory references are incomplete")
        fixture = tuple(_event_dict(event) for event in trajectory.events)
        _normalise_fixture(list(fixture))
        asset_version = _asset_version(trajectory)
        evidence = _trajectory_evidence(trajectory)
        fixture_digest = _fixture_digest(task_input, fixture, asset_version)
        expected_evidence = dict(evidence)
        expected_evidence["fixture_digest"] = fixture_digest
        return cls(
            id=_case_id(source_id, project_reference),
            source_trajectory_id=source_id,
            task_input=task_input,
            fixture_reference=f"trajectory:{source_id}",
            project_reference=project_reference,
            session_reference=session_reference,
            asset_version=asset_version,
            expected_evidence=expected_evidence,
            fixture_result=dict(expected_evidence),
            fixture=fixture,
            runner_config=runner_config.to_dict(),
            split=split,
            case_group=_case_group(task_input),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReplayCase":
        if not isinstance(value, Mapping):
            raise ReplayError("replay case must be an object")
        try:
            reject_unsafe_paths(value)
        except UnsafePathError as exc:
            raise ReplayError("replay case contains an unsafe path") from exc
        if _contains_marker(value, "[REDACTED:") or _contains_marker(value, "[TRUNCATED"):
            raise ReplayError("replay case contains redacted or truncated evidence")
        sanitized = _strict_redactor().sanitize(value)
        if sanitized != value:
            raise ReplayError("replay case contains unredacted or unbounded content")
        schema_version = value.get("schema_version", REPLAY_SCHEMA_VERSION)
        if schema_version != REPLAY_SCHEMA_VERSION:
            raise ReplayError("unsupported replay case schema version")
        case_id = _text(value.get("id"), "case.id", limit=80)
        if case_id is None or not _CASE_ID.fullmatch(case_id):
            raise ReplayError("case.id is unsafe")
        split = _text(value.get("split"), "case.split", limit=32)
        if split not in VALID_SPLITS:
            raise ReplayError("case.split is unsupported")
        source_id = _text(value.get("source_trajectory_id"), "case.source_trajectory_id", limit=160)
        task_input = _text(value.get("task_input"), "case.task_input", limit=16_384)
        fixture_reference = _text(value.get("fixture_reference"), "case.fixture_reference", limit=512)
        project_reference = _text(value.get("project_reference"), "case.project_reference", limit=512)
        session_reference = _text(value.get("session_reference"), "case.session_reference", limit=512)
        asset_version = _text(value.get("asset_version"), "case.asset_version", limit=512)
        case_group = _text(value.get("case_group"), "case.case_group", limit=160)
        if None in (source_id, task_input, fixture_reference, project_reference, session_reference, asset_version, case_group):
            raise ReplayError("replay case references are incomplete")
        if _safe_source_label(source_id, 0, _strict_redactor()) != source_id:
            raise ReplayError("case.source_trajectory_id is unsafe")
        runner_config = RunnerConfig.from_mapping(value.get("runner_config")).to_dict()
        expected = _normalise_evidence(value.get("expected_evidence"), "case.expected_evidence")
        fixture_result = _normalise_evidence(value.get("fixture_result"), "case.fixture_result")
        if (
            expected["evaluation_status"] != "evaluated"
            or fixture_result["evaluation_status"] != "evaluated"
            or expected["outcome_status"] == "unevaluated"
            or fixture_result["outcome_status"] == "unevaluated"
        ):
            raise ReplayError("replay case has incomplete evaluation evidence")
        fixture = _normalise_fixture(value.get("fixture"))
        actual_fixture_digest = _fixture_digest(task_input, fixture, asset_version)
        if expected.get("fixture_digest") != actual_fixture_digest or fixture_result.get("fixture_digest") != actual_fixture_digest:
            raise ReplayError("replay fixture digest does not match its content")
        case = cls(
            id=case_id,
            source_trajectory_id=source_id,
            task_input=task_input,
            fixture_reference=fixture_reference,
            project_reference=project_reference,
            session_reference=session_reference,
            asset_version=asset_version,
            expected_evidence=expected,
            fixture_result=fixture_result,
            fixture=fixture,
            runner_config=runner_config,
            split=split,
            case_group=case_group,
            schema_version=schema_version,
        )
        if case.id != _case_id(case.source_trajectory_id, case.project_reference):
            raise ReplayError("case.id does not match its source identity")
        if case.fixture_reference != f"trajectory:{case.source_trajectory_id}":
            raise ReplayError("case.fixture_reference does not match its source identity")
        if len(_canonical(case.to_dict()).encode("utf-8")) > MAX_FIXTURE_BYTES * 2:
            raise ReplayError("replay case exceeds size limit")
        return case

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "source_trajectory_id": self.source_trajectory_id,
            "task_input": self.task_input,
            "fixture_reference": self.fixture_reference,
            "project_reference": self.project_reference,
            "session_reference": self.session_reference,
            "asset_version": self.asset_version,
            "expected_evidence": self.expected_evidence,
            "fixture_result": self.fixture_result,
            "fixture": list(self.fixture),
            "runner_config": self.runner_config,
            "split": self.split,
            "case_group": self.case_group,
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()

    def mutation_input(self) -> dict[str, Any]:
        """Return only the development data allowed to inform a candidate."""

        if self.split != DEVELOPMENT_SPLIT:
            raise ReplayError("held-out cases cannot be used as mutation inputs")
        return {
            "case_id": self.id,
            "source_trajectory_id": self.source_trajectory_id,
            "task_input": self.task_input,
            "fixture_reference": self.fixture_reference,
            "project_reference": self.project_reference,
            "session_reference": self.session_reference,
            "asset_version": self.asset_version,
            "fixture": list(self.fixture),
            "expected_evidence": self.expected_evidence,
        }


@dataclass(frozen=True)
class ReplayExclusion:
    """Safe reason why a historical trajectory was not made replayable."""

    source: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "reason": self.reason}


def _canonical_case_groups(cases: Iterable[ReplayCase]) -> tuple[ReplayCase, ...]:
    ordered = tuple(sorted(cases, key=lambda item: item.id))
    parent = list(range(len(ordered)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    seen_keys: dict[str, int] = {}
    for index, case in enumerate(ordered):
        for key in (
            _case_group(case.task_input),
            _session_group(case.project_reference, case.session_reference),
        ):
            previous = seen_keys.get(key)
            if previous is not None:
                union(index, previous)
            else:
                seen_keys[key] = index

    components: dict[int, list[str]] = {}
    for index, case in enumerate(ordered):
        components.setdefault(find(index), []).append(case.id)
    labels = {
        root: "group-" + hashlib.sha256("\0".join(sorted(case_ids)).encode("utf-8")).hexdigest()
        for root, case_ids in components.items()
    }
    return tuple(replace(case, case_group=labels[find(index)]) for index, case in enumerate(ordered))


def _assign_splits(cases: Iterable[ReplayCase], seed: int, held_out_fraction: float) -> tuple[ReplayCase, ...]:
    grouped = _canonical_case_groups(cases)
    groups = sorted({case.case_group for case in grouped})
    held_out_groups: set[str] = set()
    if held_out_fraction > 0.0 and len(groups) > 1:
        ranked = sorted(
            groups,
            key=lambda group: hashlib.sha256(f"{seed}\0{group}".encode("utf-8")).hexdigest(),
        )
        held_out_groups = {
            group
            for group in groups
            if int(hashlib.sha256(f"{seed}\0{group}".encode("utf-8")).hexdigest(), 16) / 2**256 < held_out_fraction
        }
        if not held_out_groups:
            held_out_groups.add(ranked[0])
        elif len(held_out_groups) == len(groups):
            held_out_groups.remove(ranked[-1])
    return tuple(
        sorted(
            (
                replace(case, split=HELD_OUT_SPLIT if case.case_group in held_out_groups else DEVELOPMENT_SPLIT)
                for case in grouped
            ),
            key=lambda item: item.id,
        )
    )


def _suite_identity(
    seed: int,
    held_out_fraction: float,
    runner_config: Mapping[str, Any],
    cases: Iterable[ReplayCase],
    exclusions: Iterable[ReplayExclusion],
) -> dict[str, Any]:
    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "seed": seed,
        "held_out_fraction": held_out_fraction,
        "runner_config": dict(runner_config),
        "cases": [case.to_dict() for case in sorted(cases, key=lambda item: item.id)],
        "exclusions": [item.to_dict() for item in sorted(exclusions, key=lambda item: (item.source, item.reason))],
    }


def _suite_id(
    seed: int,
    held_out_fraction: float,
    runner_config: Mapping[str, Any],
    cases: Iterable[ReplayCase],
    exclusions: Iterable[ReplayExclusion],
) -> str:
    digest = hashlib.sha256(
        _canonical(_suite_identity(seed, held_out_fraction, runner_config, cases, exclusions)).encode("utf-8")
    ).hexdigest()[:24]
    return f"suite-{digest}"


@dataclass(frozen=True)
class ReplaySuite:
    """A reproducible, split-aware collection of replay cases."""

    id: str
    seed: int
    held_out_fraction: float
    runner_config: dict[str, Any]
    cases: tuple[ReplayCase, ...]
    exclusions: tuple[ReplayExclusion, ...]
    schema_version: int = REPLAY_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReplaySuite":
        if not isinstance(value, Mapping):
            raise ReplayError("replay suite must be an object")
        if _contains_marker(value, "[REDACTED:") or _contains_marker(value, "[TRUNCATED"):
            raise ReplayError("replay suite contains redacted or truncated evidence")
        sanitized = _strict_redactor().sanitize(value)
        if sanitized != value:
            raise ReplayError("replay suite contains unredacted or unbounded content")
        if value.get("schema_version", REPLAY_SCHEMA_VERSION) != REPLAY_SCHEMA_VERSION:
            raise ReplayError("unsupported replay suite schema version")
        suite_id = _text(value.get("id"), "suite.id", limit=80)
        if suite_id is None or not suite_id.startswith("suite-"):
            raise ReplayError("suite.id is unsafe")
        seed = value.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ReplayError("suite.seed must be an integer")
        held_out_fraction = value.get("held_out_fraction")
        if isinstance(held_out_fraction, bool) or not isinstance(held_out_fraction, (int, float)):
            raise ReplayError("suite.held_out_fraction must be a number")
        held_out_fraction = float(held_out_fraction)
        if not math.isfinite(held_out_fraction) or not 0.0 <= held_out_fraction < 1.0:
            raise ReplayError("suite.held_out_fraction is invalid")
        runner_config = RunnerConfig.from_mapping(value.get("runner_config")).to_dict()
        raw_cases = value.get("cases")
        if not isinstance(raw_cases, list) or len(raw_cases) > MAX_CASES:
            raise ReplayError("suite.cases must be a bounded list")
        cases = tuple(sorted((ReplayCase.from_mapping(item) for item in raw_cases), key=lambda item: item.id))
        if len({case.id for case in cases}) != len(cases):
            raise ReplayError("suite case IDs must be unique")
        if len({case.source_trajectory_id for case in cases}) != len(cases):
            raise ReplayError("suite source trajectory IDs must be unique")
        if any(case.runner_config != runner_config for case in cases):
            raise ReplayError("case runner configuration differs from suite configuration")
        expected_cases = _assign_splits(cases, seed, held_out_fraction)
        if any(
            (case.case_group, case.split) != (expected.case_group, expected.split)
            for case, expected in zip(cases, expected_cases)
        ):
            raise ReplayError("replay case grouping or split membership is inconsistent")
        raw_exclusions = value.get("exclusions", [])
        if not isinstance(raw_exclusions, list) or len(raw_exclusions) > MAX_CASES:
            raise ReplayError("suite.exclusions must be a bounded list")
        exclusions: list[ReplayExclusion] = []
        for item in raw_exclusions:
            if not isinstance(item, Mapping):
                raise ReplayError("suite exclusions must be objects")
            source = _text(item.get("source"), "exclusion.source", limit=160)
            reason = _text(item.get("reason"), "exclusion.reason", limit=500)
            if source is None or reason is None:
                raise ReplayError("suite exclusion is incomplete")
            if _safe_source_label(source, 0) != source:
                raise ReplayError("suite exclusion source is unsafe")
            exclusions.append(ReplayExclusion(source, reason))
        expected_id = _suite_id(seed, held_out_fraction, runner_config, cases, exclusions)
        if suite_id != expected_id:
            raise ReplayError("replay suite identity does not match its contents")
        return cls(
            suite_id,
            seed,
            held_out_fraction,
            runner_config,
            cases,
            tuple(sorted(exclusions, key=lambda item: (item.source, item.reason))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "seed": self.seed,
            "held_out_fraction": self.held_out_fraction,
            "runner_config": self.runner_config,
            "cases": [case.to_dict() for case in self.cases],
            "exclusions": [item.to_dict() for item in self.exclusions],
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()

    @property
    def development_cases(self) -> tuple[ReplayCase, ...]:
        return tuple(case for case in self.cases if case.split == DEVELOPMENT_SPLIT)

    @property
    def held_out_cases(self) -> tuple[ReplayCase, ...]:
        return tuple(case for case in self.cases if case.split == HELD_OUT_SPLIT)

    def mutation_inputs(self) -> tuple[dict[str, Any], ...]:
        return tuple(case.mutation_input() for case in self.development_cases)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "digest": self.digest(),
            "seed": self.seed,
            "held_out_fraction": self.held_out_fraction,
            "cases": len(self.cases),
            "development": len(self.development_cases),
            "held_out": len(self.held_out_cases),
            "excluded": len(self.exclusions),
            "runner_config": self.runner_config,
        }


def build_replay_suite(
    records: Iterable[Mapping[str, Any]],
    *,
    seed: int = 0,
    held_out_fraction: float = 0.2,
    runner_config: Mapping[str, Any] | None = None,
    redactor: Redactor | None = None,
) -> ReplaySuite:
    """Build a stable replay suite from sanitized or reconstructed records."""

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ReplayError("seed must be an integer")
    if isinstance(held_out_fraction, bool) or not isinstance(held_out_fraction, (int, float)):
        raise ReplayError("held_out_fraction must be a number")
    fraction = float(held_out_fraction)
    if not math.isfinite(fraction) or not 0.0 <= fraction < 1.0:
        raise ReplayError("held_out_fraction must be from 0 to less than 1")
    config = RunnerConfig.from_mapping(runner_config)
    active_redactor = redactor or Redactor.from_environment()
    cases: list[ReplayCase] = []
    exclusions: list[ReplayExclusion] = []
    seen_sources: set[str] = set()
    for index, raw in enumerate(records):
        if index >= MAX_CASES:
            exclusions.append(ReplayExclusion(f"input-{index}", "replay suite exceeds the case limit"))
            break
        if not isinstance(raw, Mapping):
            exclusions.append(ReplayExclusion(f"input-{index}", "trajectory is not an object"))
            continue
        source = _safe_source_label(raw.get("id"), index, active_redactor)
        record_redactor = active_redactor.fork()
        try:
            trajectory = Trajectory.from_mapping(raw, record_redactor)
        except UnsafePathError:
            exclusions.append(ReplayExclusion(source, "unsafe path in trajectory evidence"))
            continue
        except RecordValidationError:
            if record_redactor.stats.redacted > 0 or _contains_marker(raw, "[REDACTED:"):
                reason = "secret-bearing evidence was redacted"
            elif record_redactor.stats.truncated > 0 or _contains_marker(raw, "[TRUNCATED"):
                reason = "trajectory evidence was truncated"
            else:
                reason = "incomplete or invalid trajectory evidence"
            exclusions.append(ReplayExclusion(source, reason))
            continue
        except (TypeError, ValueError, OverflowError, RecursionError):
            if record_redactor.stats.redacted > 0 or _contains_marker(raw, "[REDACTED:"):
                reason = "secret-bearing evidence was redacted"
            elif record_redactor.stats.truncated > 0 or _contains_marker(raw, "[TRUNCATED"):
                reason = "trajectory evidence was truncated"
            else:
                reason = "incomplete or invalid trajectory evidence"
            exclusions.append(ReplayExclusion(source, reason))
            continue
        canonical_source = trajectory.id
        if canonical_source in seen_sources:
            exclusions.append(ReplayExclusion(source, "duplicate trajectory identifier"))
            continue
        seen_sources.add(canonical_source)
        if record_redactor.stats.redacted > 0 or _record_redaction_stat(raw, "redacted") > 0 or _contains_marker(raw, "[REDACTED:"):
            exclusions.append(ReplayExclusion(source, "secret-bearing evidence was redacted"))
            continue
        metadata = raw.get("metadata")
        capture_truncated = isinstance(metadata, Mapping) and bool(metadata.get("capture_truncated"))
        if (
            record_redactor.stats.truncated > 0
            or _record_redaction_stat(raw, "truncated") > 0
            or capture_truncated
            or _contains_marker(raw, "[TRUNCATED")
        ):
            exclusions.append(ReplayExclusion(source, "trajectory evidence was truncated"))
            continue
        if trajectory.outcome_status == "unevaluated" or trajectory.evaluation.status != "evaluated":
            exclusions.append(ReplayExclusion(source, "incomplete evaluation evidence"))
            continue
        safe_source = _safe_source_label(trajectory.id, index, active_redactor)
        try:
            case = ReplayCase.from_trajectory(
                trajectory,
                split=DEVELOPMENT_SPLIT,
                runner_config=config,
                source_id=safe_source,
            )
        except ReplayError:
            exclusions.append(ReplayExclusion(source, "incomplete replay case evidence"))
            continue
        cases.append(case)

    assigned = _assign_splits(cases, seed, fraction)
    exclusions_tuple = tuple(sorted(exclusions, key=lambda item: (item.source, item.reason)))
    return ReplaySuite(
        id=_suite_id(seed, fraction, config.to_dict(), assigned, exclusions_tuple),
        seed=seed,
        held_out_fraction=fraction,
        runner_config=config.to_dict(),
        cases=assigned,
        exclusions=exclusions_tuple,
    )


_PROCESS_TOOLS = frozenset(
    {"bash", "cmd", "powershell", "sh", "shell", "run_command", "run_tests", "execute", "python", "pytest", "node", "npm", "git"}
)
_NETWORK_TOOLS = frozenset({"browser", "browser_open", "curl", "fetch", "http_request", "web_fetch", "wget"})
_SAFE_FIXTURE_TOOLS = frozenset(
    {"read_file", "search_files", "list_files", "read_config", "validate_json", "run_formatter"}
)


def _event_requests_process(event: Mapping[str, Any]) -> bool:
    if event.get("type") != "tool_call":
        return False
    tool = str(event.get("tool_name") or "").strip().lower()
    return tool in _PROCESS_TOOLS or tool not in _SAFE_FIXTURE_TOOLS


def _event_requests_network(event: Mapping[str, Any]) -> bool:
    if event.get("type") != "tool_call":
        return False
    tool = str(event.get("tool_name") or "").strip().lower()
    if tool in _NETWORK_TOOLS or any(marker in tool for marker in ("http", "web", "browser", "fetch", "curl", "wget", "network")):
        return True
    input_text = _canonical(event.get("input"))
    return "http://" in input_text.lower() or "https://" in input_text.lower()


def _result_digest(case: ReplayCase, status: str, reason: str, evidence: Mapping[str, Any], budget: Mapping[str, Any]) -> str:
    payload = {
        "case_id": case.id,
        "case_digest": case.digest(),
        "status": status,
        "reason": reason,
        "evidence": evidence,
        "budget": budget,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReplayRunResult:
    """One deterministic fixture execution result."""

    case_id: str
    split: str
    status: str
    reason: str
    evidence: dict[str, Any]
    budget: dict[str, Any]
    result_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "split": self.split,
            "status": self.status,
            "reason": self.reason,
            "evidence": self.evidence,
            "budget": self.budget,
            "result_digest": self.result_digest,
        }


class DeterministicFixtureRunner:
    """Replay recorded fixture events without subprocesses or network access."""

    def run(self, case: ReplayCase) -> ReplayRunResult:
        try:
            config = RunnerConfig.from_mapping(case.runner_config)
        except ReplayError as exc:
            evidence = case.fixture_result
            budget = {"tokens_used": 0, "processes_used": 0, "network_requests": 0, "duration_ms": 0}
            reason = str(exc)
            return ReplayRunResult(case.id, case.split, "incomplete", reason, evidence, budget, _result_digest(case, "incomplete", reason, evidence, budget))

        payload = {"task_input": case.task_input, "fixture": list(case.fixture), "asset_version": case.asset_version}
        tokens_used = max(1, math.ceil(len(_canonical(payload).encode("utf-8")) / 4))
        processes_used = sum(1 for event in case.fixture if _event_requests_process(event))
        network_requests = sum(1 for event in case.fixture if _event_requests_network(event))
        duration_ms = sum(int(event.get("duration_ms") or 0) for event in case.fixture)
        budget = {
            "tokens_used": tokens_used,
            "token_budget": config.token_budget,
            "processes_used": processes_used,
            "process_budget": config.process_budget,
            "network_requests": network_requests,
            "network": config.network,
            "duration_ms": duration_ms,
            "timeout_ms": config.timeout_ms,
        }
        reason = "fixture evidence matched"
        status = "passed"
        current_fixture_digest = _fixture_digest(case.task_input, case.fixture, case.asset_version)
        if (
            case.fixture_result["evaluation_status"] != "evaluated"
            or case.expected_evidence["evaluation_status"] != "evaluated"
            or case.fixture_result["outcome_status"] == "unevaluated"
            or case.expected_evidence["outcome_status"] == "unevaluated"
        ):
            status, reason = "incomplete", "incomplete evaluation evidence"
        elif (
            case.fixture_result.get("fixture_digest") != current_fixture_digest
            or case.expected_evidence.get("fixture_digest") != current_fixture_digest
        ):
            status, reason = "failed", "fixture changed since suite build"
        elif network_requests:
            status, reason = "incomplete", "network access is disabled"
        elif processes_used > config.process_budget:
            status, reason = "incomplete", "process budget exceeded"
        elif tokens_used > config.token_budget:
            status, reason = "incomplete", "token budget exceeded"
        elif duration_ms > config.timeout_ms:
            status, reason = "incomplete", "fixture timeout exceeded"
        elif case.fixture_result != case.expected_evidence:
            status, reason = "failed", "fixture evidence did not match expected evidence"
        return ReplayRunResult(
            case.id,
            case.split,
            status,
            reason,
            case.fixture_result,
            budget,
            _result_digest(case, status, reason, case.fixture_result, budget),
        )


def run_replay_suite(
    suite: ReplaySuite,
    *,
    split: str | None = None,
    case_id: str | None = None,
    runner: DeterministicFixtureRunner | None = None,
) -> tuple[ReplayRunResult, ...]:
    """Run selected cases; held-out cases are never converted to mutation inputs."""

    if split not in (None, "all", *VALID_SPLITS):
        raise ReplayError("unsupported replay run split")
    selected = suite.cases
    if split not in (None, "all"):
        selected = tuple(case for case in selected if case.split == split)
    if case_id is not None:
        selected = tuple(case for case in selected if case.id == case_id)
        if not selected:
            raise ReplayError("replay case was not found")
    if not selected:
        raise ReplayError("no replay cases selected")
    active_runner = runner or DeterministicFixtureRunner()
    results: list[ReplayRunResult] = []
    for case in selected:
        try:
            results.append(active_runner.run(case))
        except Exception:
            evidence = case.fixture_result
            budget = {
                "tokens_used": 0,
                "processes_used": 0,
                "network_requests": 0,
                "duration_ms": 0,
            }
            reason = "runner failure"
            results.append(
                ReplayRunResult(
                    case.id,
                    case.split,
                    "incomplete",
                    reason,
                    evidence,
                    budget,
                    _result_digest(case, "incomplete", reason, evidence, budget),
                )
            )
    return tuple(results)


def default_replay_path(root: str | Path, suite: ReplaySuite) -> Path:
    return Path(root) / "data" / "replay" / f"{suite.id}.json"


def write_replay_suite(path: str | Path, suite: ReplaySuite) -> Path:
    target = Path(path)
    try:
        _reject_link_ancestors(target)
    except SkillAssetError as exc:
        raise ReplayError("replay suite path contains a symlink or junction") from exc
    if _is_link(target):
        raise ReplayError("replay suite target is a symlink or junction")
    payload = json.dumps(suite.to_dict(), ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    if len(payload.encode("utf-8")) > MAX_SUITE_BYTES:
        raise ReplayError("replay suite exceeds size limit")
    atomic_write_text(target, payload)
    return target


def load_replay_suite(path: str | Path) -> ReplaySuite:
    source = Path(path)
    try:
        _reject_link_ancestors(source)
    except SkillAssetError as exc:
        raise ReplayError("replay suite path contains a symlink or junction") from exc
    if _is_link(source) or not source.is_file():
        raise ReplayError("replay suite file is unavailable")
    try:
        payload = read_bounded(source, MAX_SUITE_BYTES).decode("utf-8")
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayError("replay suite file is invalid") from exc
    suite = ReplaySuite.from_mapping(value)
    if source.stem.startswith("suite-") and source.stem != suite.id:
        raise ReplayError("replay suite filename does not match its identity")
    return suite

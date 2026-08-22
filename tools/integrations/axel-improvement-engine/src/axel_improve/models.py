"""Versioned trajectory and outcome models for the local ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from .errors import RecordValidationError
from .redaction import Redactor, reject_unsafe_paths


SCHEMA_VERSION = 1
VALID_TRAJECTORY_STATUSES = frozenset({"success", "failure", "partial", "cancelled", "unevaluated"})
VALID_EVALUATION_STATUSES = frozenset({"evaluated", "unevaluated"})
VALID_EVENT_TYPES = frozenset(
    {"user_prompt", "assistant_message", "tool_call", "tool_result", "file_change", "validator"}
)


def utc_now() -> str:
    """Return a stable UTC timestamp for persisted records."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _required_text(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RecordValidationError(f"field '{key}' must be a non-empty string")
    return value.strip()


def _optional_text(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RecordValidationError(f"field '{key}' must be a string or null")
    text = value.strip()
    return text or None


def _bounded_nonnegative_int(mapping: Mapping[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 86_400_000:
        raise RecordValidationError(f"field '{key}' must be an integer from 0 to 86400000")
    return value


def _score(mapping: Mapping[str, Any], key: str = "score") -> float | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecordValidationError(f"field '{key}' must be a number or null")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise RecordValidationError(f"field '{key}' must be between 0 and 1")
    return result


@dataclass(frozen=True)
class ValidatorEvidence:
    """Result of a bounded validator associated with an outcome."""

    name: str
    passed: bool
    score: float | None = None
    details: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ValidatorEvidence":
        name = _required_text(value, "name")
        passed = value.get("passed")
        if not isinstance(passed, bool):
            raise RecordValidationError("validator field 'passed' must be boolean")
        details = _optional_text(value, "details")
        return cls(name=name, passed=passed, score=_score(value), details=details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "score": self.score,
            "details": self.details,
        }


@dataclass(frozen=True)
class EvaluationEvidence:
    """Evaluation status and validator evidence for a trajectory."""

    status: str = "unevaluated"
    evaluator: str | None = None
    score: float | None = None
    validators: tuple[ValidatorEvidence, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "EvaluationEvidence":
        if value is None:
            return cls()
        status = value.get("status", "unevaluated")
        if not isinstance(status, str) or status not in VALID_EVALUATION_STATUSES:
            raise RecordValidationError("evaluation status must be evaluated or unevaluated")
        validators_raw = value.get("validators", [])
        if not isinstance(validators_raw, list):
            raise RecordValidationError("evaluation validators must be a list")
        validators: list[ValidatorEvidence] = []
        for item in validators_raw:
            if not isinstance(item, Mapping):
                raise RecordValidationError("each evaluation validator must be an object")
            validators.append(ValidatorEvidence.from_mapping(item))
        evaluator = _optional_text(value, "evaluator")
        score = _score(value)
        if status == "evaluated" and evaluator is None and not validators:
            raise RecordValidationError("evaluated outcome requires an evaluator or validator")
        return cls(status=status, evaluator=evaluator, score=score, validators=tuple(validators))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "evaluator": self.evaluator,
            "score": self.score,
            "validators": [validator.to_dict() for validator in self.validators],
        }


@dataclass(frozen=True)
class TrajectoryEvent:
    """One normalized action or observation within a trajectory."""

    id: str
    event_type: str
    tool_name: str | None = None
    input: Any = None
    output: Any = None
    status: str | None = None
    duration_ms: int | None = None
    created_at: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TrajectoryEvent":
        event_id = _required_text(value, "id")
        event_type = value.get("event_type", value.get("type"))
        if not isinstance(event_type, str) or event_type not in VALID_EVENT_TYPES:
            raise RecordValidationError("event field 'type' is unsupported")
        tool_name = _optional_text(value, "tool_name")
        status = _optional_text(value, "status")
        duration_ms = _bounded_nonnegative_int(value, "duration_ms")
        created_at = _optional_text(value, "created_at")
        event_input = value.get("input", value.get("args", value.get("payload")))
        event_output = value.get("output", value.get("result"))
        return cls(
            id=event_id,
            event_type=event_type,
            tool_name=tool_name,
            input=event_input,
            output=event_output,
            status=status,
            duration_ms=duration_ms,
            created_at=created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.event_type,
            "tool_name": self.tool_name,
            "input": self.input,
            "output": self.output,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class Trajectory:
    """A sanitized, versioned agent execution trajectory."""

    id: str
    host: str
    project_id: str
    session_id: str
    task: str
    events: tuple[TrajectoryEvent, ...]
    outcome_status: str
    outcome_summary: str
    evaluation: EvaluationEvidence
    host_version: str | None = None
    user_correction: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str | None = None
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        redactor: Redactor | None = None,
    ) -> "Trajectory":
        """Sanitize and validate one untrusted JSON-compatible record."""

        if not isinstance(value, Mapping):
            raise RecordValidationError("trajectory must be an object")
        reject_unsafe_paths(value)
        active_redactor = redactor or Redactor.from_environment()
        sanitized = active_redactor.sanitize(value)
        if not isinstance(sanitized, dict):
            raise RecordValidationError("trajectory must be an object")
        return cls._from_sanitized(sanitized, active_redactor.stats.as_dict())

    @classmethod
    def _from_sanitized(cls, value: Mapping[str, Any], redaction_stats: dict[str, int]) -> "Trajectory":
        schema_version = value.get("schema_version", SCHEMA_VERSION)
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise RecordValidationError("schema version must be an integer")
        if schema_version != SCHEMA_VERSION:
            raise RecordValidationError("unsupported schema version")

        trajectory_id = _required_text(value, "id")
        host = _required_text(value, "host")
        project_id = _required_text(value, "project_id")
        session_id = _required_text(value, "session_id")
        task = _required_text(value, "task")

        events_raw = value.get("events", value.get("actions"))
        if not isinstance(events_raw, list) or not events_raw:
            raise RecordValidationError("trajectory must contain a non-empty events list")
        events: list[TrajectoryEvent] = []
        event_ids: set[str] = set()
        for item in events_raw:
            if not isinstance(item, Mapping):
                raise RecordValidationError("each trajectory event must be an object")
            event = TrajectoryEvent.from_mapping(item)
            if event.id in event_ids:
                raise RecordValidationError("trajectory event IDs must be unique")
            event_ids.add(event.id)
            events.append(event)

        outcome = value.get("outcome")
        if not isinstance(outcome, Mapping):
            raise RecordValidationError("trajectory outcome must be an object")
        outcome_status = outcome.get("status")
        if not isinstance(outcome_status, str) or outcome_status not in VALID_TRAJECTORY_STATUSES:
            raise RecordValidationError("outcome status is unsupported")
        outcome_summary = _required_text(outcome, "summary")
        evaluation_value = outcome.get("evaluation")
        if evaluation_value is None and any(key in outcome for key in ("evaluation_status", "evaluator")):
            evaluation_value = {
                "status": outcome.get("evaluation_status", "unevaluated"),
                "evaluator": outcome.get("evaluator"),
                "score": outcome.get("score"),
                "validators": outcome.get("validators", []),
            }
        if evaluation_value is not None and not isinstance(evaluation_value, Mapping):
            raise RecordValidationError("evaluation must be an object or null")
        evaluation = EvaluationEvidence.from_mapping(evaluation_value)

        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict):
            raise RecordValidationError("metadata must be an object")
        metadata = dict(metadata)
        metadata["redaction"] = redaction_stats

        return cls(
            id=trajectory_id,
            host=host,
            project_id=project_id,
            session_id=session_id,
            task=task,
            events=tuple(events),
            outcome_status=outcome_status,
            outcome_summary=outcome_summary,
            evaluation=evaluation,
            host_version=_optional_text(value, "host_version"),
            user_correction=_optional_text(outcome, "user_correction"),
            metadata=metadata,
            created_at=_optional_text(value, "created_at"),
            schema_version=schema_version,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical sanitized interchange representation."""

        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "host": self.host,
            "host_version": self.host_version,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "task": self.task,
            "events": [event.to_dict() for event in self.events],
            "outcome": {
                "status": self.outcome_status,
                "summary": self.outcome_summary,
                "evaluation": self.evaluation.to_dict(),
                "user_correction": self.user_correction,
            },
            "metadata": self.metadata or {},
            "created_at": self.created_at,
        }

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        """Return the content digest used for idempotency and conflict checks."""

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

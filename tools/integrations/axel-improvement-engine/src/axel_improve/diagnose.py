"""Deterministic-first diagnosis of recurring improvement opportunities."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from .errors import AxelImproveError, RecordValidationError
from .models import Trajectory
from .taxonomy import DIAGNOSIS_CLASSES, DiagnosisClass


UNKNOWN_TARGET = "unknown"
DIAGNOSIS_STATUSES = frozenset({"eligible", "one_off", "unresolved", "retired"})
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "check",
        "file",
        "for",
        "from",
        "in",
        "of",
        "the",
        "to",
        "with",
    }
)


class DiagnosisProvider(Protocol):
    """Optional model seam. Implementations return untrusted structured data."""

    def diagnose(self, evidence: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ProviderOutputError(AxelImproveError):
    """Raised when optional provider output fails the diagnosis contract."""


@dataclass(frozen=True)
class DiagnosisConfig:
    min_recurrence: int = 2
    min_target_confidence: float = 0.5
    similarity_threshold: float = 0.8

    def __post_init__(self) -> None:
        if self.min_recurrence < 2:
            raise ValueError("min_recurrence must be at least 2")
        if not 0.0 < self.min_target_confidence <= 1.0:
            raise ValueError("min_target_confidence must be greater than 0 and at most 1")
        if not 0.0 <= self.similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be between 0 and 1")


@dataclass(frozen=True)
class Diagnosis:
    """One deterministic evidence group and its promotion eligibility."""

    id: str
    signature: str
    diagnosis_class: str
    target: str
    target_confidence: float
    recurrence_count: int
    promotable: bool
    status: str
    rationale: str
    trajectory_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    provider_assessment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "signature": self.signature,
            "diagnosis_class": self.diagnosis_class,
            "class": self.diagnosis_class,
            "target": self.target,
            "target_confidence": self.target_confidence,
            "confidence": self.target_confidence,
            "recurrence_count": self.recurrence_count,
            "promotable": self.promotable,
            "status": self.status,
            "rationale": self.rationale,
            "trajectory_ids": list(self.trajectory_ids),
            "evidence_ids": list(self.evidence_ids),
            "event_ids": list(self.event_ids),
            "provider_assessment": self.provider_assessment,
        }


@dataclass(frozen=True)
class _Signal:
    signature: str
    tokens: frozenset[str]
    diagnosis_class: str
    target: str
    target_confidence: float
    rationale: str
    trajectory_id: str
    event_ids: tuple[str, ...]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:24]


def _words(*values: object) -> frozenset[str]:
    text = " ".join(str(value).lower() for value in values if value is not None)
    return frozenset(word for word in _WORD_RE.findall(text) if word not in _STOP_WORDS)


def _normalized(*values: object) -> str:
    return "-".join(sorted(_words(*values))) or "unknown"


def _metadata_text(metadata: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _trajectory_signal(trajectory: Trajectory) -> _Signal:
    metadata = trajectory.metadata or {}
    explicit_class = _metadata_text(metadata, "diagnosis_class", "class")
    explicit_key = _metadata_text(metadata, "diagnosis_key", "signal", "signature")
    explicit_target = _metadata_text(metadata, "asset_target", "target", "affected_asset")
    events = trajectory.events
    failed_tools = [event.tool_name for event in events if event.status in {"failed", "error"} and event.tool_name]
    evaluation_validators = list(trajectory.evaluation.validators)
    validators = [event.tool_name for event in events if event.event_type == "validator" and event.tool_name]
    failed_validators = [event.tool_name for event in events if event.event_type == "validator" and event.status in {"failed", "error"}]
    failed_validators.extend(item.name for item in evaluation_validators if not item.passed)
    validators.extend(failed_validators)
    tool_names = [event.tool_name for event in events if event.tool_name]
    has_assistant = any(event.event_type == "assistant_message" for event in events)
    correction = trajectory.user_correction

    if explicit_class in DIAGNOSIS_CLASSES:
        diagnosis_class = explicit_class
        signature = explicit_key or f"{explicit_class}:{_normalized(trajectory.task)}"
        target = explicit_target or UNKNOWN_TARGET
        confidence = 0.95 if explicit_target else 0.0
        rationale = "Explicit diagnosis metadata supplied by the sanitized trajectory."
    elif correction:
        diagnosis_class = DiagnosisClass.SKILL.value
        signature = f"skill:correction:{_normalized(correction)}"
        target = explicit_target or UNKNOWN_TARGET
        confidence = 0.95 if explicit_target else 0.0
        rationale = "A user correction is direct evidence for a reusable procedural skill improvement."
    elif validators:
        diagnosis_class = DiagnosisClass.VALIDATOR.value
        validator_key = _normalized(*failed_validators) if failed_validators else _normalized(*validators)
        signature = f"validator:failure:{validator_key}" if failed_validators else f"validator:event:{validator_key}"
        target = explicit_target or UNKNOWN_TARGET
        confidence = 0.95 if explicit_target else 0.0
        rationale = "Repeated validator evidence indicates a reusable validation opportunity."
    elif failed_tools:
        tool = failed_tools[0]
        diagnosis_class = DiagnosisClass.TOOL_FAILURE.value
        signature = f"tool-failure:{tool}"
        target = explicit_target or f"tool:{tool}"
        confidence = 0.9
        rationale = f"The {tool} tool produced a failed or error-status event."
    elif any(tool == "search_files" for tool in tool_names):
        diagnosis_class = DiagnosisClass.ROUTING.value
        signature = "routing:search-files"
        target = explicit_target or "tool:search_files"
        confidence = 0.85
        rationale = "Repeated search activity may identify a routing or retrieval improvement."
    elif any(tool == "read_file" for tool in tool_names):
        diagnosis_class = DiagnosisClass.MEMORY.value
        signature = "memory:read-file"
        target = explicit_target or "tool:read_file"
        confidence = 0.85
        rationale = "Repeated file-retrieval activity may identify a reusable memory or context improvement."
    elif trajectory.outcome_status in {"failure", "partial"}:
        diagnosis_class = DiagnosisClass.RECOVERY.value
        signature = f"recovery:{trajectory.outcome_status}:{_normalized(trajectory.task, trajectory.outcome_summary)}"
        target = explicit_target or UNKNOWN_TARGET
        confidence = 0.95 if explicit_target else 0.0
        rationale = "Repeated incomplete or failed outcomes may identify a recovery-procedure improvement."
    elif has_assistant:
        diagnosis_class = DiagnosisClass.SKILL.value
        signature = f"skill:assistant-response:{_normalized(trajectory.task)}"
        target = explicit_target or UNKNOWN_TARGET
        confidence = 0.0
        rationale = "Repeated assistant response patterns may identify a procedural skill improvement."
    else:
        diagnosis_class = DiagnosisClass.ONE_OFF.value
        signature = f"one-off:{_normalized(trajectory.task)}"
        target = explicit_target or UNKNOWN_TARGET
        confidence = 0.95 if explicit_target else 0.0
        rationale = "No recurring improvement pattern was identified from this trajectory."

    tokens = _words(signature, trajectory.task, trajectory.outcome_summary, *tool_names)
    return _Signal(
        signature=signature,
        tokens=tokens,
        diagnosis_class=diagnosis_class,
        target=target,
        target_confidence=confidence,
        rationale=rationale,
        trajectory_id=trajectory.id,
        event_ids=tuple(event.id for event in events),
    )


def _similarity(left: _Signal, right: _Signal) -> float:
    if not left.tokens or not right.tokens:
        return 0.0
    return len(left.tokens & right.tokens) / len(left.tokens | right.tokens)


def _group_signals(signals: Iterable[_Signal], threshold: float) -> list[list[_Signal]]:
    ordered = sorted(signals, key=lambda item: (item.signature, item.trajectory_id))
    parents = list(range(len(ordered)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left, signal in enumerate(ordered):
        for right in range(left + 1, len(ordered)):
            other = ordered[right]
            same_target = signal.target == other.target
            same_class = signal.diagnosis_class == other.diagnosis_class
            equivalent = signal.signature == other.signature and same_class and same_target
            similar = same_class and same_target and _similarity(signal, other) >= threshold
            if equivalent or similar:
                union(left, right)

    grouped: dict[int, list[_Signal]] = {}
    for index, signal in enumerate(ordered):
        grouped.setdefault(find(index), []).append(signal)
    return [grouped[key] for key in sorted(grouped, key=lambda root: (grouped[root][0].signature, grouped[root][0].trajectory_id))]


def _diagnosis_from_group(group: list[_Signal], config: DiagnosisConfig) -> Diagnosis:
    ordered = sorted(group, key=lambda item: item.trajectory_id)
    first = ordered[0]
    trajectory_ids = tuple(dict.fromkeys(item.trajectory_id for item in ordered))
    event_ids = tuple(dict.fromkeys(event_id for item in ordered for event_id in item.event_ids))
    evidence_ids = tuple(trajectory_ids + event_ids)
    recurrence = len(trajectory_ids)
    target = next((item.target for item in ordered if item.target != UNKNOWN_TARGET), UNKNOWN_TARGET)
    confidence = max(item.target_confidence for item in ordered if item.target == target)
    if recurrence < config.min_recurrence:
        status = "one_off"
    elif target == UNKNOWN_TARGET or confidence < config.min_target_confidence:
        status = "unresolved"
    else:
        status = "eligible"
    promotable = status == "eligible" and first.diagnosis_class != DiagnosisClass.ONE_OFF.value
    base_signature = min(item.signature for item in ordered)
    signature = f"{first.diagnosis_class}|{target}|{base_signature}"
    diagnosis_id = f"diagnosis-{_hash(signature)}"
    return Diagnosis(
        id=diagnosis_id,
        signature=signature,
        diagnosis_class=first.diagnosis_class,
        target=target,
        target_confidence=confidence,
        recurrence_count=recurrence,
        promotable=promotable,
        status=status,
        rationale=first.rationale,
        trajectory_ids=trajectory_ids,
        evidence_ids=evidence_ids,
        event_ids=event_ids,
    )


def validate_provider_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a provider result without executing or trusting its content."""

    if not isinstance(value, Mapping):
        raise ProviderOutputError("provider diagnosis must be an object")
    allowed = {"diagnosis_class", "target", "confidence", "rationale"}
    if set(value) - allowed:
        raise ProviderOutputError("provider diagnosis contains unsupported fields")
    diagnosis_class = value.get("diagnosis_class")
    target = value.get("target")
    confidence = value.get("confidence")
    rationale = value.get("rationale")
    if not isinstance(diagnosis_class, str) or diagnosis_class not in DIAGNOSIS_CLASSES:
        raise ProviderOutputError("provider diagnosis class is unsupported")
    if not isinstance(target, str) or not target.strip() or len(target) > 200:
        raise ProviderOutputError("provider diagnosis target is invalid")
    injection = re.compile(r"(?i)(ignore (?:all|any|previous) instructions|system prompt|developer message|(?:run|execute) (?:the )?(?:shell|command)|<\/?(?:system|assistant|tool)>)")
    if injection.search(target):
        raise ProviderOutputError("provider diagnosis target is invalid")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ProviderOutputError("provider diagnosis confidence is invalid")
    numeric_confidence = float(confidence)
    if not math.isfinite(numeric_confidence) or not 0.0 <= numeric_confidence <= 1.0:
        raise ProviderOutputError("provider diagnosis confidence is invalid")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 2000 or "\x00" in rationale or injection.search(rationale):
        raise ProviderOutputError("provider diagnosis rationale is invalid")
    return {
        "diagnosis_class": diagnosis_class,
        "target": target.strip(),
        "confidence": numeric_confidence,
        "rationale": rationale.strip(),
    }


def diagnose_trajectories(
    trajectories: Iterable[Trajectory | Mapping[str, Any]],
    config: DiagnosisConfig | None = None,
    provider: DiagnosisProvider | None = None,
) -> list[Diagnosis]:
    """Group sanitized trajectories and optionally attach validated provider evidence."""

    active_config = config or DiagnosisConfig()
    normalized: list[Trajectory] = []
    seen_ids: set[str] = set()
    for item in trajectories:
        if isinstance(item, Trajectory):
            normalized.append(item)
        elif isinstance(item, Mapping):
            normalized.append(Trajectory.from_mapping(item))
        else:
            raise RecordValidationError("diagnosis input must contain trajectories")
        if normalized[-1].id in seen_ids:
            raise RecordValidationError("diagnosis input contains duplicate trajectory IDs")
        seen_ids.add(normalized[-1].id)
    diagnoses = [
        _diagnosis_from_group(group, active_config)
        for group in _group_signals((_trajectory_signal(item) for item in normalized), active_config.similarity_threshold)
    ]
    diagnoses.sort(key=lambda item: item.id)
    if provider is None:
        return diagnoses
    enriched: list[Diagnosis] = []
    for diagnosis in diagnoses:
        try:
            raw_result = provider.diagnose(diagnosis.to_dict())
        except ProviderOutputError:
            raise
        except Exception as exc:
            raise ProviderOutputError("provider diagnosis failed") from exc
        result = validate_provider_result(raw_result)
        enriched.append(replace(diagnosis, provider_assessment=result))
    return enriched

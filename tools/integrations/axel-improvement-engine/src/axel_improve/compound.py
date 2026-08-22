"""Bounded batch compounding of trajectory evidence into reviewable candidates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .candidates import CandidateError, propose_skill_candidate
from .diagnose import Diagnosis, DiagnosisConfig, UNKNOWN_TARGET, diagnose_trajectories
from .errors import LedgerError
from .models import Trajectory
from .skills import SkillAssetError, SkillBank, _is_link, _reject_link_ancestors, content_digest, parse_frontmatter
from .store import atomic_write_text


COMPOUND_SCHEMA_VERSION = 1
MAX_TRAJECTORIES = 10_000
MAX_DIAGNOSES = 1_000
MAX_WORKERS = 16
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_REPORT_ITEMS = 1_000
MAX_REPORT_SELECTIONS = 100
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class CompoundError(ValueError):
    """Raised when a batch cannot be safely compounded."""


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, allow_nan=False, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise CompoundError("compound data must be JSON-compatible") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:24]


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(value: str, fallback: str = "improvement") -> str:
    words = re.findall(r"[A-Za-z0-9]+", value.lower())
    result = "-".join(words)[:48].strip("-")
    return result or fallback


def _safe_text(value: Any, field: str, limit: int = 8_192) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompoundError(f"{field} must be a non-empty string")
    result = value.strip()
    if "\x00" in result or len(result.encode("utf-8")) > limit:
        raise CompoundError(f"{field} is unsafe or exceeds its size limit")
    return result


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        result = method()
        if isinstance(result, Mapping):
            return dict(result)
    raise CompoundError("compound event must be a mapping or to_dict object")


@dataclass(frozen=True)
class CompoundConfig:
    """Bounds and diagnosis settings for one deterministic batch."""

    seed: int = 0
    min_recurrence: int = 2
    min_target_confidence: float = 0.5
    similarity_threshold: float = 0.8
    max_workers: int = 4

    def __post_init__(self) -> None:
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise CompoundError("seed must be an integer")
        if self.min_recurrence < 2:
            raise CompoundError("min_recurrence must be at least two")
        if not 0.5 <= self.min_target_confidence <= 1.0:
            raise CompoundError("min_target_confidence must be between 0.5 and one")
        if not 0.0 <= self.similarity_threshold <= 1.0:
            raise CompoundError("similarity_threshold must be between zero and one")
        if not 1 <= self.max_workers <= MAX_WORKERS:
            raise CompoundError(f"max_workers must be between one and {MAX_WORKERS}")


@dataclass(frozen=True)
class GeneratedProposal:
    """One generator output before reflection and curation."""

    proposal_id: str
    diagnosis_id: str
    signature: str
    identity: str
    diagnosis_class: str
    target_label: str
    candidate_target: str | None
    target_class: str
    new_skill_name: str
    trajectory_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    rationale: str
    proposed_content: str
    evaluation_rules: tuple[dict[str, str], ...]
    target_confidence: float
    recurrence_count: int
    provider_assessment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "diagnosis_id": self.diagnosis_id,
            "signature": self.signature,
            "identity": self.identity,
            "diagnosis_class": self.diagnosis_class,
            "target_label": self.target_label,
            "candidate_target": self.candidate_target,
            "target_class": self.target_class,
            "new_skill_name": self.new_skill_name,
            "trajectory_ids": list(self.trajectory_ids),
            "evidence_ids": list(self.evidence_ids),
            "event_ids": list(self.event_ids),
            "rationale": self.rationale,
            "proposed_content": self.proposed_content,
            "evaluation_rules": list(self.evaluation_rules),
            "target_confidence": self.target_confidence,
            "recurrence_count": self.recurrence_count,
            "provider_assessment": self.provider_assessment,
        }


@dataclass(frozen=True)
class ReflectionGroup:
    """Reflector output for one capability identity."""

    group_id: str
    identity: str
    proposal_ids: tuple[str, ...]
    variant_digests: tuple[str, ...]
    conflict: bool
    conflict_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "identity": self.identity,
            "proposal_ids": list(self.proposal_ids),
            "variant_digests": list(self.variant_digests),
            "conflict": self.conflict,
            "conflict_reason": self.conflict_reason,
        }


@dataclass(frozen=True)
class CuratedProposal:
    """One conflict-aware proposal that will be sent to the candidate boundary."""

    proposal_id: str
    group_id: str
    identity: str
    diagnosis_ids: tuple[str, ...]
    diagnosis_class: str
    target_label: str
    candidate_target: str | None
    target_class: str
    new_skill_name: str
    trajectory_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    run_id: str
    signatures: tuple[str, ...]
    rationale: str
    proposed_content: str
    evaluation_rules: tuple[dict[str, str], ...]
    target_confidence: float
    recurrence_count: int
    conflict: bool
    provider_assessment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "group_id": self.group_id,
            "identity": self.identity,
            "diagnosis_ids": list(self.diagnosis_ids),
            "diagnosis_class": self.diagnosis_class,
            "target_label": self.target_label,
            "candidate_target": self.candidate_target,
            "target_class": self.target_class,
            "new_skill_name": self.new_skill_name,
            "trajectory_ids": list(self.trajectory_ids),
            "evidence_ids": list(self.evidence_ids),
            "event_ids": list(self.event_ids),
            "run_id": self.run_id,
            "signatures": list(self.signatures),
            "rationale": self.rationale,
            "proposed_content": self.proposed_content,
            "evaluation_rules": list(self.evaluation_rules),
            "target_confidence": self.target_confidence,
            "recurrence_count": self.recurrence_count,
            "conflict": self.conflict,
            "provider_assessment": self.provider_assessment,
        }

    def diagnosis_mapping(self) -> dict[str, Any]:
        return {
            "id": self.proposal_id,
            "signature": self.identity,
            "class": self.target_class,
            "target": self.candidate_target,
            "confidence": self.target_confidence,
            "target_confidence": self.target_confidence,
            "status": "eligible",
            "promotable": True,
            "recurrence_count": self.recurrence_count,
            "trajectory_ids": list(self.trajectory_ids),
            "evidence_ids": list(self.evidence_ids),
            "event_ids": list(self.event_ids),
            "rationale": self.rationale,
            "proposed_content": self.proposed_content,
            "evaluation_rules": list(self.evaluation_rules),
            "new_skill": self.candidate_target is None,
            "provider_assessment": self.provider_assessment,
            "compound_provenance": {
                "run_id": self.run_id,
                "group_id": self.group_id,
                "diagnosis_ids": list(self.diagnosis_ids),
                "signatures": list(self.signatures),
                "conflict": self.conflict,
            },
        }


@dataclass(frozen=True)
class CandidateOutcome:
    """Safe summary of candidate materialization."""

    proposal_id: str
    identity: str
    action: str
    status: str
    reason: str
    candidate_id: str | None
    candidate_digest: str | None
    candidate_path: str | None
    conflict: bool
    diagnosis_ids: tuple[str, ...]
    trajectory_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "identity": self.identity,
            "action": self.action,
            "status": self.status,
            "reason": self.reason,
            "candidate_id": self.candidate_id,
            "candidate_digest": self.candidate_digest,
            "candidate_path": self.candidate_path,
            "conflict": self.conflict,
            "diagnosis_ids": list(self.diagnosis_ids),
            "trajectory_ids": list(self.trajectory_ids),
        }


class CompoundGenerator:
    """Generate independent local proposals without mutating skill assets."""

    _CLASS_MAP = {
        "memory": "memory",
        "skill": "skill",
        "routing": "routing",
        "validator": "validator",
        "playbook": "playbook",
        "recovery-procedure": "recovery-procedure",
        "template": "template",
        "tool-failure": "recovery-procedure",
    }

    def __init__(
        self,
        skill_bank: SkillBank,
        *,
        seed: int,
        max_workers: int,
        min_recurrence: int,
        min_target_confidence: float,
    ) -> None:
        self.seed = seed
        self.max_workers = max_workers
        self.min_recurrence = min_recurrence
        self.min_target_confidence = min_target_confidence
        self.active_assets = skill_bank.active_assets()

    def _active_target(self, target: str) -> Any | None:
        requested = target.strip().replace("\\", "/").rstrip("/")
        matches = [
            asset
            for asset in self.active_assets
            if requested in {asset.name, asset.relative_path.as_posix(), asset.relative_path.parent.as_posix()}
        ]
        if len(matches) > 1:
            raise CompoundError(f"diagnosis target is ambiguous: {target}")
        return matches[0] if matches else None

    def _generate_one(self, diagnosis: Diagnosis) -> GeneratedProposal:
        target_label = diagnosis.target
        active = None if target_label == UNKNOWN_TARGET else self._active_target(target_label)
        candidate_target = active.relative_path.as_posix() if active is not None else None
        target_class = self._CLASS_MAP.get(diagnosis.diagnosis_class)
        if target_class is None:
            raise CompoundError(f"diagnosis class is not compoundable: {diagnosis.diagnosis_class}")
        name_basis = target_label if active is None else active.name
        new_skill_name = f"compound-{_slug(name_basis, target_class)}"
        identity_target = candidate_target or f"new:{new_skill_name}"
        identity = f"{target_class}|{identity_target}"
        proposal_id = f"compound-{_hash({'seed': self.seed, 'identity': identity, 'diagnosis': diagnosis.id})}"
        rule_id = f"compound-{_hash({'identity': identity})[:16]}"
        rationale = _safe_text(diagnosis.rationale, "diagnosis rationale", limit=4_000)
        title = _slug(target_label if target_label != UNKNOWN_TARGET else diagnosis.diagnosis_class, target_class).replace("-", " ").title()
        if active is not None:
            proposed_content = (
                active.content.rstrip()
                + "\n\n## Compound Improvement\n"
                + f"Apply the recurring {target_class} improvement for {title}.\n\n"
                + f"Learned pattern: {rationale}\n\n"
                + "Verify the behavior against the cited replay evidence before approval.\n"
            )
        else:
            proposed_content = (
                "---\n"
                f"name: {new_skill_name}\n"
                f"description: Consolidated {target_class} improvement for {title}\n"
                "---\n\n"
                f"# {title}\n"
                f"Apply the recurring {target_class} improvement.\n\n"
                f"Learned pattern: {rationale}\n\n"
                "Verify the behavior against the cited replay evidence before approval.\n"
            )
        return GeneratedProposal(
            proposal_id,
            diagnosis.id,
            diagnosis.signature,
            identity,
            diagnosis.diagnosis_class,
            target_label,
            candidate_target,
            target_class,
            new_skill_name,
            tuple(sorted(diagnosis.trajectory_ids)),
            tuple(sorted(diagnosis.evidence_ids)),
            tuple(sorted(diagnosis.event_ids)),
            rationale,
            proposed_content,
            ({"id": rule_id, "type": "compound"},),
            diagnosis.target_confidence,
            len(set(diagnosis.trajectory_ids)),
            diagnosis.provider_assessment,
        )

    def generate(self, diagnoses: Iterable[Diagnosis]) -> tuple[GeneratedProposal, ...]:
        eligible = sorted(
            (
                item
                for item in diagnoses
                if item.status == "eligible"
                and item.promotable
                and len(set(item.trajectory_ids)) >= self.min_recurrence
                and item.target_confidence >= self.min_target_confidence
            ),
            key=lambda item: item.id,
        )
        if not eligible:
            return ()
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(eligible))) as executor:
            proposals = list(executor.map(self._generate_one, eligible))
        return tuple(sorted(proposals, key=lambda item: item.proposal_id))


class CompoundReflector:
    """Group proposals by capability identity and expose conflicts explicitly."""

    def reflect(self, proposals: Iterable[GeneratedProposal]) -> tuple[ReflectionGroup, ...]:
        grouped: dict[str, list[GeneratedProposal]] = {}
        for proposal in sorted(proposals, key=lambda item: item.proposal_id):
            grouped.setdefault(proposal.identity, []).append(proposal)
        result: list[ReflectionGroup] = []
        for identity, values in sorted(grouped.items()):
            variants = tuple(sorted({content_digest(item.proposed_content) for item in values}))
            conflict = len(variants) > 1
            result.append(
                ReflectionGroup(
                    f"group-{_hash(identity)}",
                    identity,
                    tuple(item.proposal_id for item in values),
                    variants,
                    conflict,
                    "multiple local proposals target the same capability with different content" if conflict else None,
                )
            )
        return tuple(result)


def _frontmatter_block(content: str) -> str:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return ""
    closing = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    return "" if closing is None else "".join(lines[: closing + 1]).rstrip()


def _body(content: str) -> str:
    try:
        _, body = parse_frontmatter(content)
    except SkillAssetError as exc:
        raise CompoundError("generated proposal has invalid frontmatter") from exc
    return body.strip()


class CompoundCurator:
    """Consolidate each reflection group into one reviewable proposal."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    def curate(
        self,
        proposals: Iterable[GeneratedProposal],
        groups: Iterable[ReflectionGroup],
    ) -> tuple[CuratedProposal, ...]:
        by_id = {item.proposal_id: item for item in proposals}
        curated: list[CuratedProposal] = []
        for group in groups:
            values = [by_id[item] for item in group.proposal_ids]
            first = values[0]
            contents: list[str] = []
            for value in values:
                body = _body(value.proposed_content)
                if body and body not in contents:
                    contents.append(body)
            if group.conflict:
                header = _frontmatter_block(first.proposed_content)
                proposed_content = header + "\n\n" + "\n\n".join(contents) + "\n"
                rationale = "Consolidated conflicting local proposals: " + "; ".join(
                    sorted({item.rationale for item in values})
                )
            else:
                proposed_content = first.proposed_content
                rationale = first.rationale
            diagnosis_ids = tuple(sorted({item.diagnosis_id for item in values}))
            trajectory_ids = tuple(sorted({item for value in values for item in value.trajectory_ids}))
            evidence_ids = tuple(sorted({item for value in values for item in value.evidence_ids}))
            event_ids = tuple(sorted({item for value in values for item in value.event_ids}))
            signatures = tuple(sorted({item.signature for item in values}))
            assessments = [item.provider_assessment for item in values if item.provider_assessment is not None]
            provider_assessment: dict[str, Any] | None = None
            if assessments:
                distinct = sorted({_canonical(item) for item in assessments})
                provider_assessment = (
                    assessments[0]
                    if len(distinct) == 1
                    else {"assessments": [json.loads(item) for item in distinct]}
                )
            proposal_id = f"compound-{_hash({'seed': group.group_id, 'digests': group.variant_digests})}"
            curated.append(
                CuratedProposal(
                    proposal_id,
                    group.group_id,
                    group.identity,
                    diagnosis_ids,
                    first.diagnosis_class,
                    first.target_label,
                    first.candidate_target,
                    first.target_class,
                    first.new_skill_name,
                    trajectory_ids,
                    evidence_ids,
                    event_ids,
                    self.run_id,
                    signatures,
                    _safe_text(rationale, "curated rationale", limit=8_000),
                    proposed_content,
                    first.evaluation_rules,
                    max(item.target_confidence for item in values),
                    len(trajectory_ids),
                    group.conflict,
                    provider_assessment,
                )
            )
        return tuple(sorted(curated, key=lambda item: item.proposal_id))


@dataclass(frozen=True)
class CompoundReport:
    """Machine-readable and human-readable summary of one batch."""

    run_id: str
    seed: int
    trajectory_count: int
    diagnosis_count: int
    eligible_diagnosis_count: int
    learned: tuple[dict[str, Any], ...]
    changes: tuple[dict[str, Any], ...]
    candidates: tuple[dict[str, Any], ...]
    rejections: tuple[dict[str, Any], ...]
    evaluations: tuple[dict[str, Any], ...]
    retrieval: tuple[dict[str, Any], ...]
    promotions: tuple[dict[str, Any], ...]
    rollbacks: tuple[dict[str, Any], ...]
    artifacts: Mapping[str, str]
    created_at: str
    schema_version: int = COMPOUND_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "seed": self.seed,
            "trajectory_count": self.trajectory_count,
            "diagnosis_count": self.diagnosis_count,
            "eligible_diagnosis_count": self.eligible_diagnosis_count,
            "learned": list(self.learned),
            "changes": list(self.changes),
            "candidates": list(self.candidates),
            "rejections": list(self.rejections),
            "evaluations": list(self.evaluations),
            "retrieval": list(self.retrieval),
            "promotions": list(self.promotions),
            "rollbacks": list(self.rollbacks),
            "artifacts": dict(self.artifacts),
            "created_at": self.created_at,
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()

    def to_markdown(self) -> str:
        lines = [
            f"# Compound Improvement Report: `{_markdown_text(self.run_id)}`",
            "",
            f"- Trajectories: {self.trajectory_count}",
            f"- Diagnoses: {self.diagnosis_count} ({self.eligible_diagnosis_count} eligible)",
            f"- Seed: {self.seed}",
            "",
            "## What Axel Learned",
            "",
        ]
        if self.learned:
            lines.extend(
                f"- `{_markdown_text(item['diagnosis_id'])}` ({_markdown_text(item['diagnosis_class'])}): "
                f"{_markdown_text(item['rationale'])}"
                for item in self.learned
            )
        else:
            lines.append("- No eligible recurring pattern was found.")
        lines.extend(["", "## What Changed", ""])
        if self.changes:
            for item in self.changes:
                conflict = "; conflict consolidated" if item.get("conflict") else ""
                lines.append(
                    f"- `{_markdown_text(item['identity'])}` -> {_markdown_text(item['status'])} "
                    f"({_markdown_text(item['reason'])}){conflict}; "
                    f"evidence: {_markdown_text(', '.join(item['trajectory_ids']) or 'none')}"
                )
        else:
            lines.append("- No candidate changes were produced.")
        lines.extend(["", "## Candidate Outcomes", "", "| Candidate | Status | Relation | Reason |", "|---|---|---|---|"])
        if self.candidates:
            lines.extend(
                f"| `{_markdown_text(item.get('candidate_id') or item['proposal_id'])}` | "
                f"{_markdown_text(item['status'])} | {_markdown_text(item['identity'])} | "
                f"{_markdown_text(item['reason'])} |"
                for item in self.candidates
            )
        else:
            lines.append("| none | none | none | No candidates |")
        lines.extend(["", "## Rejections", ""])
        if self.rejections:
            lines.extend(
                f"- `{_markdown_text(item.get('candidate_id') or item.get('proposal_id', 'unknown'))}`: "
                f"{_markdown_text(item.get('reason', 'rejected'))}"
                for item in self.rejections
            )
        else:
            lines.append("- No candidates were rejected by the batch boundary.")
        lines.extend(["", "## Performance", ""])
        if self.evaluations:
            for item in self.evaluations:
                before = item.get("before", {})
                after = item.get("after", {})
                delta = item.get("delta", {})
                lines.append(
                    f"- `{_markdown_text(item.get('evaluation_id', 'unknown'))}`: "
                    f"{_markdown_text(item.get('status', 'unknown'))}; "
                    f"before/after success {_markdown_text(before.get('task_success_rate'))} -> "
                    f"{_markdown_text(after.get('task_success_rate'))} "
                    f"(delta {_markdown_text(delta.get('task_success_rate'))}); tokens delta "
                    f"{_markdown_text(delta.get('tokens'))}; context delta "
                    f"{_markdown_text(delta.get('context_tokens'))}; cost delta "
                    f"{_markdown_text(delta.get('cost'))}"
                )
        else:
            lines.append("- No evaluations supplied yet; candidates remain reviewable and inactive.")
        lines.extend(["", "## Retrieval, Promotions, and Rollbacks", ""])
        lines.append(f"- Retrieval records: {len(self.retrieval)}")
        lines.append(f"- Promotions: {len(self.promotions)}")
        lines.append(f"- Rollbacks: {len(self.rollbacks)}")
        lines.extend(["", "## Artifacts", ""])
        for name, path in sorted(self.artifacts.items()):
            lines.append(f"- `{_markdown_text(name)}`: `{_markdown_text(path)}`")
        lines.append("")
        return "\n".join(lines)


@dataclass(frozen=True)
class CompoundRun:
    run_id: str
    seed: int
    trajectories: tuple[Trajectory, ...]
    diagnoses: tuple[Diagnosis, ...]
    generated: tuple[GeneratedProposal, ...]
    reflections: tuple[ReflectionGroup, ...]
    curated: tuple[CuratedProposal, ...]
    candidates: tuple[CandidateOutcome, ...]
    artifact_paths: Mapping[str, str]
    created_at: str

    def report(
        self,
        *,
        evaluations: Iterable[Any] = (),
        retrieval: Iterable[Any] = (),
        promotions: Iterable[Any] = (),
        rollbacks: Iterable[Any] = (),
        rejections: Iterable[Any] = (),
    ) -> CompoundReport:
        return build_compound_report(
            self,
            evaluations=evaluations,
            retrieval=retrieval,
            promotions=promotions,
            rollbacks=rollbacks,
            rejections=rejections,
        )


def _normalize_trajectories(items: Iterable[Trajectory | Mapping[str, Any]]) -> tuple[Trajectory, ...]:
    normalized: list[Trajectory] = []
    seen: set[str] = set()
    for item in items:
        trajectory = item if isinstance(item, Trajectory) else Trajectory.from_mapping(item)
        if trajectory.id in seen:
            raise CompoundError("compound input contains duplicate trajectory IDs")
        seen.add(trajectory.id)
        normalized.append(trajectory)
        if len(normalized) > MAX_TRAJECTORIES:
            raise CompoundError("compound input exceeds trajectory limit")
    return tuple(sorted(normalized, key=lambda item: (item.created_at or "", item.id)))


def _runtime_root(candidate_root: Path) -> Path:
    return candidate_root.parent.parent if candidate_root.parent.name == "skills" else candidate_root.parent


def _candidate_file_state(candidate_root: Path) -> list[dict[str, str]]:
    try:
        _reject_link_ancestors(candidate_root)
    except SkillAssetError as exc:
        raise CompoundError("compound candidate root contains a symlink or junction") from exc
    if not candidate_root.exists():
        return []
    if _is_link(candidate_root) or not candidate_root.is_dir():
        raise CompoundError("compound candidate root must be a real directory")
    state: list[dict[str, str]] = []
    for path in sorted(candidate_root.rglob("*")):
        if _is_link(path):
            raise CompoundError("compound candidate root contains a symlink or junction")
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            raise CompoundError("compound candidate state exceeds size limit")
        state.append(
            {
                "path": path.relative_to(candidate_root).as_posix(),
                "digest": content_digest(path.read_bytes()),
            }
        )
    return state


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _artifact_directory(path: Path, protected_roots: Iterable[Path]) -> Path:
    try:
        _reject_link_ancestors(path)
    except SkillAssetError as exc:
        raise CompoundError("compound artifact path contains a symlink or junction") from exc
    resolved = Path(os.path.abspath(path)).resolve()
    if any(_paths_overlap(resolved, root.resolve()) for root in protected_roots):
        raise CompoundError("compound artifacts overlap a protected skill root")
    path.mkdir(parents=True, exist_ok=True)
    if _is_link(path) or not path.is_dir():
        raise CompoundError("compound artifact directory must be real")
    return path.resolve()


def _write_artifact(path: Path, payload: Mapping[str, Any]) -> Path:
    try:
        _reject_link_ancestors(path)
    except SkillAssetError as exc:
        raise CompoundError("compound artifact path contains a symlink or junction") from exc
    if _is_link(path):
        raise CompoundError("compound artifact target is a symlink or junction")
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise CompoundError("compound artifact exceeds size limit")
    try:
        atomic_write_text(path, encoded)
    except (LedgerError, OSError) as exc:
        raise CompoundError("compound artifact cannot be written") from exc
    return path


def _materialize_candidates(
    curated: Iterable[CuratedProposal],
    skill_bank: SkillBank,
    candidate_root: Path,
) -> tuple[CandidateOutcome, ...]:
    outcomes: list[CandidateOutcome] = []
    for proposal in sorted(curated, key=lambda item: item.proposal_id):
        try:
            result = propose_skill_candidate(proposal.diagnosis_mapping(), skill_bank, candidate_root)
            outcomes.append(
                CandidateOutcome(
                    proposal.proposal_id,
                    proposal.identity,
                    result.action,
                    "proposed" if result.action == "created" else result.action,
                    result.reason,
                    result.candidate_id,
                    result.candidate_digest,
                    str(result.candidate_path) if result.candidate_path else None,
                    proposal.conflict,
                    proposal.diagnosis_ids,
                    proposal.trajectory_ids,
                )
            )
        except CandidateError as exc:
            outcomes.append(
                CandidateOutcome(
                    proposal.proposal_id,
                    proposal.identity,
                    "rejected",
                    "rejected",
                    str(exc)[:500],
                    None,
                    None,
                    None,
                    proposal.conflict,
                    proposal.diagnosis_ids,
                    proposal.trajectory_ids,
                )
            )
    return tuple(outcomes)


def compound_trajectories(
    trajectories: Iterable[Trajectory | Mapping[str, Any]],
    skill_bank: SkillBank,
    candidate_root: Path | str,
    *,
    config: CompoundConfig | None = None,
    diagnoses: Iterable[Diagnosis] | None = None,
    artifact_root: Path | str | None = None,
) -> CompoundRun:
    """Compound a bounded trajectory batch into candidates and review artifacts."""

    active_config = config or CompoundConfig()
    normalized = _normalize_trajectories(trajectories)
    if diagnoses is None:
        diagnoses_tuple = tuple(
            diagnose_trajectories(
                normalized,
                DiagnosisConfig(
                    min_recurrence=active_config.min_recurrence,
                    min_target_confidence=active_config.min_target_confidence,
                    similarity_threshold=active_config.similarity_threshold,
                ),
            )
        )
    else:
        diagnoses_tuple = tuple(diagnoses)
    diagnoses_tuple = tuple(sorted(diagnoses_tuple, key=lambda item: item.id))
    if len(diagnoses_tuple) > MAX_DIAGNOSES:
        raise CompoundError("compound diagnosis count exceeds limit")
    try:
        asset_state = [
            {
                "state": asset.state,
                "path": asset.relative_path.as_posix(),
                "digest": asset.digest,
            }
            for asset in skill_bank.scan()
        ]
    except SkillAssetError as exc:
        raise CompoundError("compound skill inventory is invalid") from exc
    run_id = f"compound-{_hash({'config': active_config.__dict__, 'trajectories': sorted(item.digest() for item in normalized), 'diagnoses': [item.to_dict() for item in diagnoses_tuple], 'assets': asset_state, 'candidate_files': _candidate_file_state(Path(candidate_root))})}"
    created_at = _timestamp()
    candidate_path = Path(candidate_root)
    runtime = _runtime_root(candidate_path)
    artifact_base = Path(artifact_root) if artifact_root is not None else runtime / "data" / "compound"
    artifact_dir = _artifact_directory(
        artifact_base / run_id,
        (Path(skill_bank.active_root), candidate_path, Path(skill_bank.approved_root) if skill_bank.approved_root else runtime / "skills" / "approved"),
    )

    generator = CompoundGenerator(
        skill_bank,
        seed=active_config.seed,
        max_workers=active_config.max_workers,
        min_recurrence=active_config.min_recurrence,
        min_target_confidence=active_config.min_target_confidence,
    )
    generated = generator.generate(diagnoses_tuple)
    _write_artifact(
        artifact_dir / "generator.json",
        {"schema_version": COMPOUND_SCHEMA_VERSION, "run_id": run_id, "seed": active_config.seed, "proposals": [item.to_dict() for item in generated]},
    )
    reflections = CompoundReflector().reflect(generated)
    _write_artifact(
        artifact_dir / "reflector.json",
        {"schema_version": COMPOUND_SCHEMA_VERSION, "run_id": run_id, "groups": [item.to_dict() for item in reflections]},
    )
    curated = CompoundCurator(run_id).curate(generated, reflections)
    candidates = _materialize_candidates(curated, skill_bank, candidate_path)
    _write_artifact(
        artifact_dir / "curator.json",
        {
            "schema_version": COMPOUND_SCHEMA_VERSION,
            "run_id": run_id,
            "proposals": [item.to_dict() for item in curated],
            "candidate_outcomes": [item.to_dict() for item in candidates],
        },
    )
    artifact_paths = {name: str(artifact_dir / f"{name}.json") for name in ("generator", "reflector", "curator")}
    return CompoundRun(run_id, active_config.seed, normalized, diagnoses_tuple, generated, reflections, curated, candidates, artifact_paths, created_at)


def _markdown_text(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("`", "\\`").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _bounded_items(values: Iterable[Any], field: str) -> list[Any]:
    result: list[Any] = []
    for index, value in enumerate(values):
        if index >= MAX_REPORT_ITEMS:
            raise CompoundError(f"{field} exceeds report item limit")
        result.append(value)
    return result


def _report_scalar(value: Any, field: str, *, limit: int = 2_048) -> Any:
    if value is None:
        return None
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        if "\x00" in value or len(value.encode("utf-8")) > limit:
            raise CompoundError(f"{field} exceeds report size limit")
        return value.strip()
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CompoundError(f"{field} must be finite")
        return value
    raise CompoundError(f"{field} must be a scalar report value")


def _report_id_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > MAX_REPORT_SELECTIONS:
        raise CompoundError(f"{field} is not a bounded identifier list")
    result = []
    for item in value:
        text = _report_scalar(item, field, limit=512)
        if not isinstance(text, str) or not text:
            raise CompoundError(f"{field} contains an invalid identifier")
        result.append(text)
    return result


def _metric_summary(value: Any, field: str) -> Any:
    return _report_scalar(value, field, limit=128)


def _evaluation_summary(value: Any) -> dict[str, Any]:
    item = _as_dict(value)
    before = item.get("baseline", {})
    after = item.get("candidate", {})
    delta = item.get("delta", {})
    metrics = ("task_success_rate", "validator_score", "cost", "tokens", "context_tokens")

    def metrics_from(source: Any, label: str) -> dict[str, Any]:
        if source is None:
            return {}
        if not isinstance(source, Mapping):
            raise CompoundError(f"evaluation {label} metrics must be an object")
        return {key: _metric_summary(source.get(key), f"evaluation {label}.{key}") for key in metrics}

    return {
        "evaluation_id": _report_scalar(item.get("id", item.get("evaluation_id")), "evaluation_id"),
        "candidate_id": _report_scalar(item.get("candidate_id"), "evaluation candidate_id"),
        "status": _report_scalar(item.get("status"), "evaluation status"),
        "before": metrics_from(before, "baseline"),
        "after": metrics_from(after, "candidate"),
        "delta": metrics_from(delta, "delta"),
    }


def _retrieval_selection_summary(value: Any) -> dict[str, Any]:
    item = _as_dict(value)
    fields = (
        "asset_key",
        "candidate_id",
        "candidate_digest",
        "candidate_provenance_digest",
        "approved_provenance_digest",
        "evaluation_id",
        "evaluation_digest",
        "asset_path",
        "section_id",
        "heading",
        "start_line",
        "end_line",
        "score",
        "lexical_score",
        "embedding_score",
        "token_count",
        "text_digest",
    )
    return {field: _report_scalar(item.get(field), f"retrieval selection {field}") for field in fields}


def _retrieval_summary(value: Any) -> dict[str, Any]:
    item = _as_dict(value)
    if isinstance(item.get("record"), Mapping):
        item = dict(item["record"])
    selections = item.get("selections", [])
    if not isinstance(selections, (list, tuple)) or len(selections) > MAX_REPORT_SELECTIONS:
        raise CompoundError("retrieval selections exceed report limit")
    return {
        "id": _report_scalar(item.get("id"), "retrieval id"),
        "task_id": _report_scalar(item.get("task_id"), "retrieval task_id"),
        "query_digest": _report_scalar(item.get("query_digest"), "retrieval query_digest"),
        "manifest_revision": _report_scalar(item.get("manifest_revision"), "retrieval manifest_revision"),
        "selection_count": len(selections),
        "selections": [_retrieval_selection_summary(selection) for selection in selections],
    }


def _candidate_summary(value: CandidateOutcome) -> dict[str, Any]:
    item = value.to_dict()
    return {
        "proposal_id": _report_scalar(item["proposal_id"], "candidate proposal_id"),
        "identity": _report_scalar(item["identity"], "candidate identity"),
        "action": _report_scalar(item["action"], "candidate action"),
        "status": _report_scalar(item["status"], "candidate status"),
        "reason": _report_scalar(item["reason"], "candidate reason"),
        "candidate_id": _report_scalar(item["candidate_id"], "candidate id"),
        "candidate_digest": _report_scalar(item["candidate_digest"], "candidate digest"),
        "candidate_path": _report_scalar(item["candidate_path"], "candidate path"),
        "conflict": _report_scalar(item["conflict"], "candidate conflict"),
        "diagnosis_ids": _report_id_list(item["diagnosis_ids"], "candidate diagnosis_ids"),
        "trajectory_ids": _report_id_list(item["trajectory_ids"], "candidate trajectory_ids"),
    }


def _lifecycle_summary(value: Any) -> dict[str, Any]:
    item = _as_dict(value)
    fields = (
        "candidate_id",
        "candidate_digest",
        "evaluation_id",
        "evaluation_digest",
        "decision",
        "status",
        "approval_path",
        "approval_digest",
        "decided_at",
        "asset_key",
        "approved_path",
        "manifest_path",
        "commit",
        "previous_candidate_id",
        "restored_candidate_id",
        "restored_digest",
        "reason",
        "operator",
    )
    return {field: _report_scalar(item.get(field), f"lifecycle {field}") for field in fields if field in item}


def _rejection_summary(value: Any) -> dict[str, Any]:
    item = _as_dict(value)
    fields = ("proposal_id", "candidate_id", "identity", "status", "reason")
    return {field: _report_scalar(item.get(field), f"rejection {field}") for field in fields if field in item}


def build_compound_report(
    run: CompoundRun,
    *,
    evaluations: Iterable[Any] = (),
    retrieval: Iterable[Any] = (),
    promotions: Iterable[Any] = (),
    rollbacks: Iterable[Any] = (),
    rejections: Iterable[Any] = (),
) -> CompoundReport:
    learned = tuple(
        {
            "diagnosis_id": item.id,
            "diagnosis_class": item.diagnosis_class,
            "target": item.target,
            "rationale": item.rationale,
            "recurrence_count": len(set(item.trajectory_ids)),
            "trajectory_ids": list(item.trajectory_ids),
        }
        for item in sorted(run.diagnoses, key=lambda value: value.id)
        if len(set(item.trajectory_ids)) >= 2
    )
    outcome_by_id = {item.proposal_id: item for item in run.candidates}
    changes: list[dict[str, Any]] = []
    for proposal in run.curated:
        outcome = outcome_by_id.get(proposal.proposal_id)
        changes.append(
            {
                "identity": proposal.identity,
                "proposal_id": proposal.proposal_id,
                "diagnosis_ids": list(proposal.diagnosis_ids),
                "trajectory_ids": list(proposal.trajectory_ids),
                "conflict": proposal.conflict,
                "status": outcome.status if outcome else "curated",
                "reason": outcome.reason if outcome else "awaiting materialization",
            }
        )
    external_rejections = _bounded_items(rejections, "rejections")
    candidates = tuple(_candidate_summary(item) for item in run.candidates)
    rejection_items = tuple(
        [_rejection_summary(item) for item in run.candidates if item.status == "rejected"]
        + [_rejection_summary(item) for item in external_rejections]
    )
    if len(rejection_items) > MAX_REPORT_ITEMS:
        raise CompoundError("rejections exceed report item limit")
    return CompoundReport(
        run.run_id,
        run.seed,
        len(run.trajectories),
        len(run.diagnoses),
        sum(len(set(item.trajectory_ids)) >= 2 and item.status == "eligible" and item.promotable for item in run.diagnoses),
        learned,
        tuple(changes),
        candidates,
        rejection_items,
        tuple(_evaluation_summary(item) for item in _bounded_items(evaluations, "evaluations")),
        tuple(_retrieval_summary(item) for item in _bounded_items(retrieval, "retrieval")),
        tuple(_lifecycle_summary(item) for item in _bounded_items(promotions, "promotions")),
        tuple(_lifecycle_summary(item) for item in _bounded_items(rollbacks, "rollbacks")),
        dict(run.artifact_paths),
        run.created_at,
    )


def default_compound_report_paths(root: Path | str, report: CompoundReport) -> tuple[Path, Path]:
    base = Path(root) / "reports"
    return base / f"{report.run_id}.json", base / f"{report.run_id}.md"


def _inferred_protected_roots(path: Path) -> tuple[Path, ...]:
    absolute = Path(os.path.abspath(path))
    for ancestor in (absolute, *absolute.parents):
        if ancestor.name == "skills":
            return tuple(ancestor / name for name in ("active", "candidates", "approved"))
    return ()


def _write_report_file(path: Path, content: str, protected_roots: tuple[Path, ...]) -> Path:
    try:
        _reject_link_ancestors(path)
    except SkillAssetError as exc:
        raise CompoundError("compound report path contains a symlink or junction") from exc
    if _is_link(path):
        raise CompoundError("compound report target is a symlink or junction")
    target = Path(os.path.abspath(path)).resolve()
    if any(_paths_overlap(target, root.resolve()) for root in (*_inferred_protected_roots(path), *protected_roots)):
        raise CompoundError("compound report overlaps a protected skill root")
    try:
        atomic_write_text(path, content)
    except (LedgerError, OSError) as exc:
        raise CompoundError("compound report cannot be written") from exc
    return path


def write_compound_report(
    report: CompoundReport,
    json_path: Path | str,
    markdown_path: Path | str,
    *,
    protected_roots: Iterable[Path | str] = (),
) -> tuple[Path, Path]:
    roots = tuple(Path(item) for item in protected_roots)
    json_target = Path(json_path)
    markdown_target = Path(markdown_path)
    if json_target.resolve() == markdown_target.resolve():
        raise CompoundError("compound JSON and Markdown reports must use different paths")
    try:
        json_content = json.dumps(report.to_dict(), allow_nan=False, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        raise CompoundError("compound report is not valid JSON") from exc
    markdown_content = report.to_markdown()
    if len(json_content.encode("utf-8")) > MAX_ARTIFACT_BYTES or len(markdown_content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise CompoundError("compound report exceeds size limit")
    return (
        _write_report_file(json_target, json_content, roots),
        _write_report_file(markdown_target, markdown_content, roots),
    )


__all__ = [
    "COMPOUND_SCHEMA_VERSION",
    "CompoundConfig",
    "CompoundCurator",
    "CompoundError",
    "CompoundGenerator",
    "CompoundReflector",
    "CompoundReport",
    "CompoundRun",
    "CandidateOutcome",
    "CuratedProposal",
    "GeneratedProposal",
    "ReflectionGroup",
    "build_compound_report",
    "compound_trajectories",
    "default_compound_report_paths",
    "write_compound_report",
]

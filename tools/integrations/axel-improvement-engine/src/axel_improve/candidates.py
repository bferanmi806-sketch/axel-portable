"""Safe, provenance-bearing skill candidate proposals.

This module is intentionally a data boundary: it validates untrusted mapping
input, writes only below a candidate root, and never executes proposed text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from .skills import SkillAssetError, SkillBank, _is_link, content_digest, parse_frontmatter, read_bounded
from .taxonomy import DIAGNOSIS_CLASSES


MAX_CANDIDATE_BODY_BYTES = 131_072
MAX_PROVENANCE_BYTES = 65_536
MAX_DIFF_BYTES = 262_144
MAX_EVIDENCE_ITEMS = 100
MAX_EVALUATION_RULES = 100
CANDIDATE_ROOT_MARKER = ".axel-candidate-root"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_STATUSES = frozenset(
    {"observed", "diagnosed", "proposed", "tested", "awaiting_approval", "approved", "rejected", "retired", "rolled_back", "suppressed"}
)
_TRANSITIONS = {
    "proposed": {"tested", "rejected", "retired"},
    "tested": {"awaiting_approval", "rejected", "retired"},
    "awaiting_approval": {"approved", "rejected"},
    "approved": {"rolled_back", "retired"},
}


class CandidateError(ValueError):
    """Raised for invalid, unsafe, or non-actionable candidate input."""


@dataclass(frozen=True)
class CandidateResult:
    """The reviewable outcome of proposal generation."""

    action: str
    candidate_id: str | None
    reason: str
    candidate_path: Path | None = None
    target_digest: str | None = None
    candidate_digest: str | None = None


def _text(value: Any, field: str, *, required: bool = True, limit: int = 8_192) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise CandidateError(f"{field} must be a non-empty string")
    text = value.strip()
    if len(text.encode("utf-8")) > limit:
        raise CandidateError(f"{field} exceeds size limit")
    return text


def _id_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CandidateError(f"{field} must be a list of identifiers")
    if len(value) > MAX_EVIDENCE_ITEMS:
        raise CandidateError(f"{field} exceeds item limit")
    result = []
    for item in value:
        text = _text(item, field, limit=160)
        if text is None or not _SAFE_ID.fullmatch(text):
            raise CandidateError(f"{field} contains an unsafe identifier")
        result.append(text)
    if not result:
        raise CandidateError(f"{field} must not be empty")
    return sorted(set(result))


def _evaluation_rules(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CandidateError("evaluation_rules must be a list")
    if len(value) > MAX_EVALUATION_RULES:
        raise CandidateError("evaluation_rules exceeds item limit")
    rules: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise CandidateError("each evaluation rule must be an object")
        rule_id = _text(item.get("id"), "evaluation rule id", limit=160)
        rule_type = _text(item.get("type"), "evaluation rule type", limit=160)
        if not _SAFE_ID.fullmatch(rule_id or "") or not _SAFE_ID.fullmatch(rule_type or ""):
            raise CandidateError("evaluation rule identifiers are unsafe")
        rules.append({"id": rule_id or "", "type": rule_type or ""})
    return rules


def _safe_candidate_id(diagnosis: Mapping[str, Any]) -> str:
    # Reserve room for the stable ``candidate-`` namespace prefix.
    raw_id = _text(diagnosis.get("id"), "id", limit=70)
    if raw_id is None or not _SAFE_ID.fullmatch(raw_id):
        raise CandidateError("id is unsafe")
    return f"candidate-{raw_id}"


def _revision_id(candidate_id: str, digest: str) -> str:
    suffix = digest[:12]
    prefix = candidate_id[: 80 - len(suffix) - 1]
    return f"{prefix}-{suffix}"


def _safe_root(root: Path, *, create: bool = True) -> Path:
    current = Path(os.path.abspath(root))
    ancestors: list[Path] = []
    while True:
        ancestors.append(current)
        if current.parent == current:
            break
        current = current.parent
    if any(_is_link(path) for path in ancestors):
        raise CandidateError("candidate root contains a symlink or junction")
    if not root.exists():
        if not create:
            return root.resolve()
        root.mkdir(parents=True, exist_ok=True)
    if _is_link(root) or not root.is_dir():
        raise CandidateError("candidate root must be a real directory")
    return root.resolve()


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _validate_candidate_boundary(root: Path, skill_bank: SkillBank) -> None:
    if skill_bank.candidate_root is None:
        raise CandidateError("skill bank must configure a candidate root")
    configured_candidate = _safe_root(skill_bank.candidate_root, create=False)
    for protected in (skill_bank.active_root, skill_bank.approved_root):
        if protected is None:
            continue
        protected_root = Path(os.path.abspath(protected)).resolve()
        if _paths_overlap(root, protected_root):
            raise CandidateError("candidate root overlaps a protected skill root")
    if root != configured_candidate:
        raise CandidateError("candidate root is outside the configured candidate directory")
    configured_candidate.mkdir(parents=True, exist_ok=True)
    marker = configured_candidate / CANDIDATE_ROOT_MARKER
    if marker.exists() or _is_link(marker):
        if _is_link(marker) or not marker.is_file():
            raise CandidateError("candidate root marker is unsafe")
    else:
        try:
            with marker.open("x", encoding="ascii", newline="\n") as handle:
                handle.write("Axel Improvement Engine candidate root\n")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if _is_link(marker) or not marker.is_file():
                raise CandidateError("candidate root marker is unsafe")


def _candidate_path(root: Path, candidate_id: str) -> Path:
    if not _SAFE_ID.fullmatch(candidate_id):
        raise CandidateError("candidate identifier is unsafe")
    candidate_path = root / candidate_id
    if candidate_path.parent != root or _is_link(candidate_path):
        raise CandidateError("candidate path escapes its root")
    return candidate_path


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def provenance_digest(record: Mapping[str, Any]) -> str:
    immutable = {
        key: value
        for key, value in record.items()
        if key not in {"status", "updated_at", "created_at", "provenance_digest"}
    }
    return hashlib.sha256(json.dumps(immutable, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _render_frontmatter(values: Mapping[str, Any]) -> str:
    lines = ["---"]
    for key, value in values.items():
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value)
            lines.append(f"{key}: [{rendered}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _normalise_candidate_skill(content: str, *, candidate_id: str, target_class: str, target: str | None, parent_digest: str | None, trajectory_ids: list[str], evidence_ids: list[str]) -> str:
    if not isinstance(content, str) or not content.strip():
        raise CandidateError("proposed_content must be a non-empty string")
    if len(content.encode("utf-8")) > MAX_CANDIDATE_BODY_BYTES:
        raise CandidateError("proposed_content exceeds size limit")
    try:
        existing_frontmatter, body = parse_frontmatter(content)
    except SkillAssetError as exc:
        raise CandidateError(str(exc)) from exc
    name = existing_frontmatter.get("name")
    if not isinstance(name, str) or not _SAFE_ID.fullmatch(name):
        if target is None:
            raise CandidateError("new skill content requires a safe frontmatter name")
        name = candidate_id
    description = str(existing_frontmatter.get("description", "Reviewable candidate skill"))[:400]
    if any(ord(character) < 32 and character not in "\t" for character in description):
        raise CandidateError("skill description contains control characters")
    metadata = {
        "name": name,
        "description": description,
        "version": "candidate",
        "target_class": target_class,
        "target": target or "new-skill",
        "parent_digest": parent_digest or "none",
        "source_trajectory_ids": trajectory_ids,
        "evidence_ids": evidence_ids,
    }
    for field in ("triggers", "dependencies"):
        value = existing_frontmatter.get(field)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        if len(values) > MAX_EVIDENCE_ITEMS or any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 160
            or any(ord(character) < 32 for character in item)
            for item in values
        ):
            raise CandidateError(f"skill frontmatter field '{field}' is unsafe")
        metadata[field] = [item.strip() for item in values]
    result = _render_frontmatter(metadata) + body.lstrip("\n")
    if len(result.encode("utf-8")) > MAX_CANDIDATE_BODY_BYTES:
        raise CandidateError("candidate skill exceeds size limit after metadata is added")
    return result


def _read_provenance(candidate_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    if not candidate_root.exists():
        return records
    for directory in candidate_root.iterdir():
        if _is_link(directory) or not directory.is_dir() or not _SAFE_ID.fullmatch(directory.name):
            continue
        sidecar = directory / "provenance.json"
        if _is_link(sidecar) or not sidecar.is_file():
            continue
        try:
            raw_sidecar = read_bounded(sidecar, MAX_PROVENANCE_BYTES)
            record = json.loads(raw_sidecar.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, SkillAssetError):
            continue
        if isinstance(record, dict):
            if record.get("provenance_digest") != provenance_digest(record):
                continue
            skill_file = directory / "SKILL.md"
            if _is_link(skill_file) or not skill_file.is_file():
                continue
            try:
                raw_skill = read_bounded(skill_file, MAX_CANDIDATE_BODY_BYTES)
                if content_digest(raw_skill) != record.get("candidate_digest"):
                    continue
            except (OSError, SkillAssetError):
                continue
            diff_file = directory / "change.diff"
            if _is_link(diff_file) or not diff_file.is_file():
                continue
            try:
                if content_digest(read_bounded(diff_file, MAX_DIFF_BYTES)) != record.get("diff_digest"):
                    continue
            except (OSError, SkillAssetError):
                continue
            records.append((directory, record))
    return records


def propose_skill_candidate(
    diagnosis: Mapping[str, Any],
    skill_bank: SkillBank,
    candidate_root: Path | str,
) -> CandidateResult:
    """Create a non-active skill candidate from a narrow diagnosis mapping.

    Accepted fields are intentionally limited to the Ticket 03 seam: ``id``,
    ``class``, ``target``, ``confidence``, ``trajectory_ids``, ``evidence_ids``,
    ``rationale``, ``proposed_content``/``proposed_diff``, and
    ``evaluation_rules``.  Unknown fields are discarded rather than persisted.
    """

    if not isinstance(diagnosis, Mapping):
        raise CandidateError("diagnosis must be a mapping")
    candidate_id = _safe_candidate_id(diagnosis)
    if diagnosis.get("status") != "eligible" or diagnosis.get("promotable") is not True:
        raise CandidateError("diagnosis is not eligible for proposal")
    target_class = _text(diagnosis.get("class", diagnosis.get("diagnosis_class")), "class", limit=80)
    if target_class not in DIAGNOSIS_CLASSES - {"one-off-incident"}:
        raise CandidateError("class is not compoundable")
    target = _text(diagnosis.get("target"), "target", required=False, limit=512)
    if target in {"unknown", "<unknown>"}:
        target = None
    confidence = diagnosis.get("confidence", diagnosis.get("target_confidence"))
    try:
        numeric_confidence = float(confidence)
    except (TypeError, ValueError, OverflowError):
        numeric_confidence = -1.0
    if isinstance(confidence, bool) or not 0.0 <= numeric_confidence <= 1.0:
        raise CandidateError("confidence must be between 0 and 1")
    recurrence_count = diagnosis.get("recurrence_count")
    if isinstance(recurrence_count, bool) or not isinstance(recurrence_count, int) or recurrence_count < 2:
        raise CandidateError("diagnosis recurrence is not eligible for proposal")
    target_confidence = diagnosis.get("target_confidence", confidence)
    try:
        numeric_target_confidence = float(target_confidence)
    except (TypeError, ValueError, OverflowError):
        numeric_target_confidence = -1.0
    if isinstance(target_confidence, bool) or not 0.5 <= numeric_target_confidence <= 1.0:
        raise CandidateError("diagnosis target confidence is not eligible for proposal")
    if target is None and diagnosis.get("new_skill") is not True:
        raise CandidateError("unknown target requires an explicit new_skill proposal")
    trajectory_ids = _id_list(diagnosis.get("trajectory_ids"), "trajectory_ids")
    evidence_ids = _id_list(diagnosis.get("evidence_ids"), "evidence_ids")
    event_ids = _id_list(diagnosis.get("event_ids"), "event_ids") if diagnosis.get("event_ids") is not None else []
    rationale = _text(diagnosis.get("rationale"), "rationale", limit=8_192)
    rules = _evaluation_rules(diagnosis.get("evaluation_rules"))
    proposed_content = diagnosis.get("proposed_content")
    proposed_diff = _text(diagnosis.get("proposed_diff"), "proposed_diff", required=False, limit=MAX_CANDIDATE_BODY_BYTES)
    if proposed_content is None:
        raise CandidateError("proposed_content is required to create a readable skill candidate")

    root = _safe_root(Path(candidate_root), create=False)
    _validate_candidate_boundary(root, skill_bank)
    try:
        target_asset = skill_bank.resolve_active_target(target) if target else None
    except SkillAssetError as exc:
        raise CandidateError(str(exc)) from exc
    if target and target_asset is None:
        raise CandidateError("target does not name an active skill")
    if not target and proposed_diff is not None:
        raise CandidateError("new skills cannot use proposed_diff")
    parent_digest = target_asset.digest if target_asset else None
    candidate_content = _normalise_candidate_skill(
        proposed_content,
        candidate_id=candidate_id,
        target_class=target_class,
        target=target_asset.relative_path.as_posix() if target_asset else None,
        parent_digest=parent_digest,
        trajectory_ids=trajectory_ids,
        evidence_ids=evidence_ids,
    )
    candidate_digest = content_digest(candidate_content)
    if target_asset is not None and content_digest(proposed_content) == target_asset.digest:
        return CandidateResult("suppressed", None, "proposed content matches the active skill", target_digest=parent_digest)
    approved_digest = content_digest(proposed_content)
    if any(asset.state == "approved" and asset.digest in {approved_digest, candidate_digest} for asset in skill_bank.scan()):
        return CandidateResult("suppressed", None, "proposed content matches an approved skill", target_digest=parent_digest)

    existing = _read_provenance(root)
    target_key = target_asset.relative_path.as_posix() if target_asset else None
    candidate_frontmatter, _ = parse_frontmatter(candidate_content)
    new_skill_name = candidate_frontmatter.get("name") if target_key is None else None
    same_target = [
        record
        for _, record in existing
        if record.get("status") not in {"rejected", "retired", "rolled_back", "suppressed"}
        and (
            (target_key is not None and record.get("target") == target_key)
            or (target_key is None and new_skill_name and record.get("new_skill_name") == new_skill_name)
        )
    ]
    for directory, record in existing:
        if record.get("status") not in {"rejected", "retired", "rolled_back", "suppressed"} and record.get("candidate_digest") == candidate_digest:
            return CandidateResult("suppressed", record.get("candidate_id"), "duplicate candidate content already exists", directory, parent_digest, candidate_digest)
    relation = "targeted_revision" if target_asset is not None else "new_skill"
    merge_candidate_for = sorted(str(record.get("candidate_id")) for record in same_target if record.get("candidate_id"))
    if merge_candidate_for:
        relation = "merge_candidate"

    candidate_path = _candidate_path(root, candidate_id)
    if candidate_path.exists():
        candidate_id = _revision_id(candidate_id, candidate_digest)
        candidate_content = _normalise_candidate_skill(
            proposed_content,
            candidate_id=candidate_id,
            target_class=target_class,
            target=target_key,
            parent_digest=parent_digest,
            trajectory_ids=trajectory_ids,
            evidence_ids=evidence_ids,
        )
        candidate_digest = content_digest(candidate_content)
        candidate_path = _candidate_path(root, candidate_id)
        if candidate_path.exists():
            raise CandidateError("candidate identifier already exists with different content")
    provider_assessment = diagnosis.get("provider_assessment")
    if provider_assessment is not None and not isinstance(provider_assessment, dict):
        raise CandidateError("provider_assessment must be an object")
    if provider_assessment is not None:
        try:
            provider_size = len(json.dumps(provider_assessment, ensure_ascii=True, sort_keys=True).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise CandidateError("provider_assessment is not JSON-compatible") from exc
        if provider_size > 16_384:
            raise CandidateError("provider_assessment exceeds size limit")
    compound_provenance = diagnosis.get("compound_provenance")
    if compound_provenance is not None:
        if not isinstance(compound_provenance, dict):
            raise CandidateError("compound provenance must be an object")
        if not isinstance(compound_provenance.get("run_id"), str) or not _SAFE_ID.fullmatch(compound_provenance["run_id"]):
            raise CandidateError("compound provenance run ID is invalid")
        if not isinstance(compound_provenance.get("group_id"), str) or not _SAFE_ID.fullmatch(compound_provenance["group_id"]):
            raise CandidateError("compound provenance group ID is invalid")
        for field in ("diagnosis_ids", "signatures"):
            values = compound_provenance.get(field)
            if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() or len(item) > 512 for item in values):
                raise CandidateError(f"compound provenance {field} is invalid")
        if not isinstance(compound_provenance.get("conflict"), bool):
            raise CandidateError("compound provenance conflict is invalid")
        try:
            compound_size = len(json.dumps(compound_provenance, ensure_ascii=True, sort_keys=True).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise CandidateError("compound provenance is not JSON-compatible") from exc
        if compound_size > 16_384:
            raise CandidateError("compound provenance exceeds size limit")
    provenance = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "status": "proposed",
        "relation": relation,
        "merge_candidate_for": merge_candidate_for,
        "target_class": target_class,
        "target": target_key,
        "new_skill_name": new_skill_name,
        "parent_digest": parent_digest,
        "candidate_digest": candidate_digest,
        "confidence": numeric_confidence,
        "diagnosis_signature": diagnosis.get("signature"),
        "diagnosis_status": diagnosis.get("status"),
        "diagnosis_promotable": diagnosis.get("promotable"),
        "diagnosis_recurrence_count": diagnosis.get("recurrence_count"),
        "diagnosis_target_confidence": numeric_target_confidence,
        "new_skill": diagnosis.get("new_skill", False),
        "trajectory_ids": trajectory_ids,
        "evidence_ids": evidence_ids,
        "event_ids": event_ids,
        "provider_assessment": provider_assessment,
        "compound_provenance": compound_provenance,
        "rationale": rationale,
        "proposed_diff": proposed_diff,
        "evaluation_rules": rules,
        "created_at": _timestamp(),
    }
    baseline = target_asset.content if target_asset else ""
    readable_diff = "".join(
        difflib.unified_diff(
            baseline.splitlines(keepends=True),
            candidate_content.splitlines(keepends=True),
            fromfile=target_key or "/dev/null",
            tofile=f"candidates/{candidate_id}/SKILL.md",
        )
    )
    provenance["diff_digest"] = content_digest(readable_diff)
    provenance["provenance_digest"] = provenance_digest(provenance)
    if len(readable_diff.encode("utf-8")) > MAX_DIFF_BYTES:
        raise CandidateError("generated diff exceeds size limit")
    if len((json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")) > MAX_PROVENANCE_BYTES:
        raise CandidateError("candidate provenance exceeds size limit")
    _write_candidate(candidate_path, candidate_content, provenance, readable_diff)
    return CandidateResult("created", candidate_id, relation.replace("_", " "), candidate_path, parent_digest, candidate_digest)


def _write_candidate(candidate_path: Path, content: str, provenance: Mapping[str, Any], readable_diff: str) -> None:
    """Create a complete candidate directory through a private staging path."""

    if candidate_path.exists() or _is_link(candidate_path):
        raise CandidateError("candidate identifier already exists")
    staging_parent = candidate_path.parent
    if _is_link(staging_parent) or not staging_parent.is_dir():
        raise CandidateError("candidate staging parent is unavailable")
    staging = staging_parent / f".{candidate_path.parent.name}.{candidate_path.name}.tmp-{uuid.uuid4().hex}"
    if staging.exists() or _is_link(staging):
        raise CandidateError("unsafe candidate staging path")
    staging.mkdir(mode=0o700)
    try:
        for filename, value in (
            ("SKILL.md", content),
            ("provenance.json", json.dumps(provenance, indent=2, sort_keys=True) + "\n"),
            ("change.diff", readable_diff),
        ):
            destination = staging / filename
            if destination.parent != staging or destination.exists() or _is_link(destination):
                raise CandidateError("unsafe candidate asset path")
            with destination.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
        if candidate_path.exists() or _is_link(candidate_path):
            raise CandidateError("candidate identifier already exists")
        staging.replace(candidate_path)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


@contextmanager
def _candidate_lock(candidate_path: Path):
    lock = candidate_path.parent / f".{candidate_path.name}.transition.lock"
    deadline = time.monotonic() + 0.75
    descriptor: int | None = None
    acquired = False
    try:
        while True:
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                    handle.write(str(os.getpid()))
                    handle.flush()
                    os.fsync(handle.fileno())
                descriptor = None
                acquired = True
                yield
                return
            except FileExistsError:
                try:
                    owner_text = read_bounded(lock, 64).decode("ascii").strip()
                    owner_pid = int(owner_text)
                    try:
                        os.kill(owner_pid, 0)
                        owner_alive = True
                    except (OSError, ProcessLookupError):
                        owner_alive = False
                    if not owner_alive:
                        lock.unlink(missing_ok=True)
                        continue
                except (OSError, SkillAssetError, UnicodeDecodeError, ValueError):
                    try:
                        if time.time() - lock.stat().st_mtime > 60:
                            lock.unlink(missing_ok=True)
                            continue
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise CandidateError("candidate transition is busy")
                time.sleep(0.01)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if acquired:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass


def transition_candidate(
    candidate_root: Path | str,
    candidate_id: str,
    status: str,
    *,
    expected_status: str | None = None,
    expected_candidate_digest: str | None = None,
    expected_parent_digest: str | None = None,
    expected_diff_digest: str | None = None,
    expected_provenance_digest: str | None = None,
    configured_candidate_root: Path | str | None = None,
    active_root: Path | str | None = None,
    approved_root: Path | str | None = None,
) -> dict[str, Any]:
    """Apply an explicit, guarded status transition to candidate provenance."""

    root = _safe_root(Path(candidate_root), create=False)
    if configured_candidate_root is None or root != _safe_root(Path(configured_candidate_root), create=False):
        raise CandidateError("configured candidate root is required for transition")
    if active_root is None or approved_root is None:
        raise CandidateError("protected active and approved roots are required for transition")
    for protected in (active_root, approved_root):
        protected_path = Path(os.path.abspath(protected)).resolve()
        if _paths_overlap(root, protected_path):
            raise CandidateError("candidate root overlaps a protected skill root")
    marker = root / CANDIDATE_ROOT_MARKER
    try:
        marker_content = read_bounded(marker, 128).decode("ascii")
    except (OSError, SkillAssetError, UnicodeDecodeError) as exc:
        raise CandidateError("candidate root marker is unavailable") from exc
    if _is_link(marker) or not marker.is_file() or marker_content != "Axel Improvement Engine candidate root\n":
        raise CandidateError("candidate root marker is unavailable")
    candidate_path = _candidate_path(root, candidate_id)
    with _candidate_lock(candidate_path):
        return _transition_candidate_unlocked(
            candidate_path,
            candidate_id,
            status,
            expected_status=expected_status,
            expected_candidate_digest=expected_candidate_digest,
            expected_parent_digest=expected_parent_digest,
            expected_diff_digest=expected_diff_digest,
            expected_provenance_digest=expected_provenance_digest,
            active_root=active_root,
            approved_root=approved_root,
        )


def _transition_candidate_unlocked(
    candidate_path: Path,
    candidate_id: str,
    status: str,
    *,
    expected_status: str | None,
    expected_candidate_digest: str | None,
    expected_parent_digest: str | None,
    expected_diff_digest: str | None,
    expected_provenance_digest: str | None,
    active_root: Path | str | None,
    approved_root: Path | str | None,
) -> dict[str, Any]:
    sidecar = candidate_path / "provenance.json"
    if not candidate_path.is_dir() or _is_link(candidate_path) or _is_link(sidecar):
        raise CandidateError("candidate provenance is unavailable")
    if status not in _STATUSES:
        raise CandidateError("unsupported candidate status")
    if expected_status is None or expected_candidate_digest is None or expected_diff_digest is None or expected_provenance_digest is None:
        raise CandidateError("external candidate state is required for transition")
    skill_file = candidate_path / "SKILL.md"
    change_file = candidate_path / "change.diff"
    if _is_link(skill_file) or not skill_file.is_file():
        raise CandidateError("candidate skill is unavailable or oversized")
    if _is_link(change_file) or not change_file.is_file():
        raise CandidateError("candidate diff is unavailable or oversized")
    try:
        raw_sidecar = read_bounded(sidecar, MAX_PROVENANCE_BYTES)
        record = json.loads(raw_sidecar.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, SkillAssetError) as exc:
        raise CandidateError("candidate provenance is invalid") from exc
    if (
        not isinstance(record, dict)
        or record.get("schema_version") != 1
        or record.get("candidate_id") != candidate_id
        or not isinstance(record.get("candidate_digest"), str)
        or not isinstance(record.get("diff_digest"), str)
        or not isinstance(record.get("provenance_digest"), str)
        or record.get("status") not in _STATUSES
    ):
        raise CandidateError("candidate provenance schema is invalid")
    target_value = record.get("target")
    if target_value is not None and (not isinstance(target_value, str) or not isinstance(record.get("parent_digest"), str) or not record.get("parent_digest")):
        raise CandidateError("targeted candidate provenance is incomplete")
    try:
        raw_skill = read_bounded(skill_file, MAX_CANDIDATE_BODY_BYTES)
        raw_diff = read_bounded(change_file, MAX_DIFF_BYTES)
    except (OSError, SkillAssetError) as exc:
        raise CandidateError("candidate assets cannot be read") from exc
    current = record.get("status")
    if current != expected_status or record.get("candidate_digest") != expected_candidate_digest:
        raise CandidateError("candidate state changed since it was read")
    if status not in _TRANSITIONS.get(current, set()):
        raise CandidateError("invalid candidate status transition")
    if status == "tested" and not _evaluation_rules(record.get("evaluation_rules")):
        raise CandidateError("candidate requires evaluation rules before testing")
    if content_digest(raw_skill) != record.get("candidate_digest"):
        raise CandidateError("candidate skill digest does not match provenance")
    if content_digest(raw_diff) != record.get("diff_digest") or record.get("diff_digest") != expected_diff_digest:
        raise CandidateError("candidate diff digest does not match provenance")
    if record.get("provenance_digest") != expected_provenance_digest or provenance_digest(record) != record.get("provenance_digest"):
        raise CandidateError("candidate provenance digest does not match provenance")
    parent_digest = record.get("parent_digest")
    if parent_digest != expected_parent_digest:
        raise CandidateError("candidate parent state changed since it was read")
    if status == "approved" and parent_digest:
        if active_root is None:
            raise CandidateError("active root is required to approve a targeted revision")
        try:
            active_asset = SkillBank(active_root).resolve_active_target(str(record.get("target", "")))
        except SkillAssetError as exc:
            raise CandidateError(str(exc)) from exc
        if active_asset is None or active_asset.digest != parent_digest:
            raise CandidateError("active skill changed since proposal")
    updated = dict(record)
    updated["status"] = status
    updated["updated_at"] = _timestamp()
    if len((json.dumps(updated, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")) > MAX_PROVENANCE_BYTES:
        raise CandidateError("candidate provenance exceeds size limit")
    temporary = candidate_path / f".provenance.tmp-{uuid.uuid4().hex}"
    if temporary.exists() or _is_link(temporary):
        raise CandidateError("unsafe candidate provenance path")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(updated, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, sidecar)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise CandidateError("candidate provenance update failed") from exc
    return updated

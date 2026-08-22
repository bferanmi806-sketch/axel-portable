"""Deterministic, fail-closed retrieval of approved skill sections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from queue import Queue
from threading import Thread
import time
from collections.abc import Mapping
from typing import Any, Protocol

from .errors import LedgerError
from .promotion import PromotionError, _load_manifest
from .skills import (
    SkillAssetError,
    _is_link,
    _reject_link_ancestors,
    content_digest,
    parse_frontmatter,
    read_bounded,
)
from .store import atomic_write_text


RETRIEVAL_SCHEMA_VERSION = 1
MAX_QUERY_CHARS = 16_384
MAX_TASK_ID_CHARS = 160
MAX_ITEMS = 100
MAX_CONTEXT_TOKENS = 16_384
MAX_SKILL_BYTES = 131_072
MAX_EMBEDDING_CALLS = 32
MAX_EMBEDDING_SECONDS = 5.0
EMBEDDING_TIMEOUT_SECONDS = 0.25
_SAFE_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")


class RetrievalError(ValueError):
    """Raised when an approved retrieval request cannot be safely completed."""


class EmbeddingProvider(Protocol):
    """Optional local scorer; no provider is required for retrieval."""

    def score(self, query: str, text: str) -> float:
        """Return a bounded similarity score from zero to one."""


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RetrievalError("retrieval data must be JSON-compatible") from exc


def _text(value: Any, field: str, *, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RetrievalError(f"{field} must be a non-empty string")
    result = value.strip()
    if "\x00" in result or len(result.encode("utf-8")) > limit:
        raise RetrievalError(f"{field} is unsafe or exceeds its size limit")
    return result


def _bounded_int(value: Any, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise RetrievalError(f"{field} must be a bounded non-negative integer")
    return value


def _threshold(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RetrievalError("threshold must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise RetrievalError("threshold must be between zero and one")
    return result


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(sorted(set(item.lower() for item in _TOKEN.findall(value))))


def _token_count(value: str) -> int:
    if not value.strip():
        return 0
    word_count = len(_TOKEN.findall(value))
    byte_count = math.ceil(len(value.encode("utf-8")) / 4)
    return max(1, word_count, byte_count)


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_approved_root(path: str | Path) -> Path | None:
    root = Path(path)
    try:
        _reject_link_ancestors(root)
    except SkillAssetError as exc:
        raise RetrievalError("approved root contains a symlink or junction") from exc
    if not root.exists():
        return None
    if _is_link(root) or not root.is_dir():
        raise RetrievalError("approved root must be a real directory")
    try:
        return root.resolve()
    except OSError as exc:
        raise RetrievalError("approved root is unavailable") from exc


def _safe_child(root: Path, relative: str, field: str) -> Path:
    candidate = root / relative
    try:
        _reject_link_ancestors(candidate)
    except SkillAssetError as exc:
        raise RetrievalError(f"{field} contains a symlink or junction") from exc
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise RetrievalError(f"{field} escapes the approved root") from exc
    if _is_link(candidate):
        raise RetrievalError(f"{field} is a symlink or junction")
    return candidate


def _metadata_text(frontmatter: Mapping[str, Any], asset_key: str, candidate_id: str) -> str:
    values: list[str] = [asset_key, candidate_id]
    for value in frontmatter.values():
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        else:
            values.append(str(value))
    return " ".join(values)


@dataclass(frozen=True)
class _Section:
    heading: str
    start_line: int
    end_line: int
    text: str


def _sections(content: str) -> tuple[_Section, ...]:
    lines = content.splitlines(keepends=True)
    body_start = 0
    if lines and lines[0].strip() == "---":
        closing = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if closing is None:
            raise RetrievalError("approved skill has unterminated frontmatter")
        body_start = closing + 1

    headings: list[tuple[int, str]] = []
    for index in range(body_start, len(lines)):
        match = _HEADING.match(lines[index].rstrip("\r\n"))
        if match is not None:
            headings.append((index, match.group(2).strip()))

    sections: list[_Section] = []
    if headings and any(line.strip() for line in lines[body_start : headings[0][0]]):
        start = body_start
        end = headings[0][0]
        text = "".join(lines[start:end]).strip()
        if text:
            sections.append(_Section("(intro)", start + 1, end, text))
    elif not headings:
        text = "".join(lines[body_start:]).strip()
        if text:
            sections.append(_Section("(document)", body_start + 1, len(lines), text))

    for position, (start, heading) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        text = "".join(lines[start:end]).strip()
        if text:
            sections.append(_Section(heading, start + 1, end, text))
    return tuple(sections)


@dataclass(frozen=True)
class _ApprovedAsset:
    asset_key: str
    candidate_id: str
    candidate_digest: str
    candidate_provenance_digest: str
    approved_provenance_digest: str
    evaluation_id: str
    evaluation_digest: str
    asset_path: str
    frontmatter: Mapping[str, Any]
    sections: tuple[_Section, ...]


def _load_approved_assets(approved_root: str | Path) -> tuple[int, tuple[_ApprovedAsset, ...]]:
    root = _safe_approved_root(approved_root)
    if root is None:
        return 0, ()
    try:
        manifest = _load_manifest(root)
    except PromotionError as exc:
        raise RetrievalError("approved manifest is invalid") from exc
    active = manifest.get("active")
    if not isinstance(active, Mapping):
        raise RetrievalError("approved manifest active entries are invalid")
    rolled_back: set[tuple[str, str]] = set()
    history = manifest.get("history", [])
    if not isinstance(history, list):
        raise RetrievalError("approved manifest history is invalid")
    for event in history:
        if not isinstance(event, Mapping):
            raise RetrievalError("approved manifest history entry is invalid")
        if event.get("action") == "rollback":
            candidate_id = event.get("candidate_id")
            candidate_digest = event.get("candidate_digest")
            if not isinstance(candidate_id, str) or not isinstance(candidate_digest, str):
                raise RetrievalError("approved rollback history is incomplete")
            rolled_back.add((candidate_id, candidate_digest))
    assets: list[_ApprovedAsset] = []
    for asset_key, entry in sorted(active.items(), key=lambda item: str(item[0])):
        if not isinstance(asset_key, str) or not isinstance(entry, Mapping):
            raise RetrievalError("approved manifest entry is invalid")
        candidate_id = entry.get("candidate_id")
        candidate_digest = entry.get("candidate_digest")
        candidate_provenance_digest = entry.get("candidate_provenance_digest")
        approved_provenance_digest = entry.get("approved_provenance_digest")
        evaluation_id = entry.get("evaluation_id")
        evaluation_digest = entry.get("evaluation_digest")
        asset_path = entry.get("asset_path")
        provenance_path = entry.get("provenance_path")
        if not all(isinstance(value, str) and value.strip() for value in (candidate_id, candidate_digest, candidate_provenance_digest, approved_provenance_digest, asset_path, provenance_path, evaluation_id, evaluation_digest)):
            raise RetrievalError("approved manifest attribution is incomplete")
        if not _SHA256.fullmatch(candidate_provenance_digest) or not _SHA256.fullmatch(approved_provenance_digest):
            raise RetrievalError("approved manifest provenance digest is invalid")
        if entry.get("asset_key") != asset_key:
            raise RetrievalError("approved manifest asset key is inconsistent")
        if (candidate_id, candidate_digest) in rolled_back:
            raise RetrievalError("approved manifest points to a rolled-back version")
        skill_path = _safe_child(root, asset_path, "approved skill")
        provenance_file = _safe_child(root, provenance_path, "approved provenance")
        try:
            raw_skill = read_bounded(skill_path, MAX_SKILL_BYTES)
            skill_content = raw_skill.decode("utf-8")
            provenance = json.loads(read_bounded(provenance_file, 65_536).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, SkillAssetError) as exc:
            raise RetrievalError("approved asset cannot be read") from exc
        if (
            not isinstance(provenance, Mapping)
            or provenance.get("status") != "approved"
            or provenance.get("candidate_id") != candidate_id
            or provenance.get("candidate_digest") != candidate_digest
            or provenance.get("asset_key") != asset_key
            or provenance.get("candidate_provenance_digest") != candidate_provenance_digest
            or provenance.get("provenance_digest") != approved_provenance_digest
            or provenance.get("evaluation_id") != evaluation_id
            or provenance.get("evaluation_digest") != evaluation_digest
            or content_digest(raw_skill) != candidate_digest
        ):
            raise RetrievalError("approved asset provenance does not match the manifest")
        try:
            frontmatter, _ = parse_frontmatter(skill_content)
            sections = _sections(skill_content)
        except SkillAssetError as exc:
            raise RetrievalError("approved skill content is invalid") from exc
        assets.append(
            _ApprovedAsset(
                asset_key,
                candidate_id,
                candidate_digest,
                candidate_provenance_digest,
                approved_provenance_digest,
                evaluation_id,
                evaluation_digest,
                asset_path,
                frontmatter,
                sections,
            )
        )
    return int(manifest.get("revision", 0)), tuple(assets)


def _lexical_score(query_terms: set[str], query: str, metadata: str, section: _Section) -> float:
    if not query_terms:
        return 0.0
    section_terms = set(_tokens(section.text))
    metadata_terms = set(_tokens(metadata))
    section_coverage = len(query_terms & section_terms) / len(query_terms)
    metadata_coverage = len(query_terms & metadata_terms) / len(query_terms)
    normalized_query = " ".join(_tokens(query))
    normalized_section = " ".join(_tokens(section.text))
    phrase_bonus = 0.1 if normalized_query and normalized_query in normalized_section else 0.0
    return min(1.0, 0.7 * section_coverage + 0.3 * metadata_coverage + phrase_bonus)


def _embedding_score(
    provider: EmbeddingProvider | None,
    query: str,
    text: str,
    timeout: float = EMBEDDING_TIMEOUT_SECONDS,
) -> float | None:
    if provider is None:
        return None
    result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)

    def run() -> None:
        try:
            result_queue.put(("value", provider.score(query, text)))
        except Exception as exc:
            result_queue.put(("error", exc))

    worker = Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise RetrievalError("embedding provider timed out")
    try:
        kind, value = result_queue.get_nowait()
    except Exception as exc:
        raise RetrievalError("embedding provider returned no score") from exc
    if kind == "error":
        raise RetrievalError("embedding provider failed") from value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise RetrievalError("embedding provider returned an invalid score")
    return float(value)


@dataclass(frozen=True)
class RetrievalSelection:
    asset_key: str
    candidate_id: str
    candidate_digest: str
    candidate_provenance_digest: str
    approved_provenance_digest: str
    evaluation_id: str
    evaluation_digest: str
    asset_path: str
    section_id: str
    heading: str
    start_line: int
    end_line: int
    score: float
    lexical_score: float
    embedding_score: float | None
    token_count: int
    text_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_key": self.asset_key,
            "candidate_id": self.candidate_id,
            "candidate_digest": self.candidate_digest,
            "candidate_provenance_digest": self.candidate_provenance_digest,
            "approved_provenance_digest": self.approved_provenance_digest,
            "evaluation_id": self.evaluation_id,
            "evaluation_digest": self.evaluation_digest,
            "asset_path": self.asset_path,
            "section_id": self.section_id,
            "heading": self.heading,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "score": self.score,
            "lexical_score": self.lexical_score,
            "embedding_score": self.embedding_score,
            "token_count": self.token_count,
            "text_digest": self.text_digest,
        }


@dataclass(frozen=True)
class RetrievalRecord:
    task_id: str
    query_digest: str
    manifest_revision: int
    threshold: float
    max_items: int
    max_tokens: int
    selections: tuple[RetrievalSelection, ...]
    created_at: str
    id: str
    schema_version: int = RETRIEVAL_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "task_id": self.task_id,
            "query_digest": self.query_digest,
            "manifest_revision": self.manifest_revision,
            "threshold": self.threshold,
            "max_items": self.max_items,
            "max_tokens": self.max_tokens,
            "selections": [item.to_dict() for item in self.selections],
            "created_at": self.created_at,
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RetrievalContextItem:
    selection: RetrievalSelection
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {**self.selection.to_dict(), "text": self.text}


@dataclass(frozen=True)
class RetrievalResult:
    record: RetrievalRecord
    context: tuple[RetrievalContextItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "context": [item.to_dict() for item in self.context],
        }


def retrieve_approved_assets(
    approved_root: str | Path,
    *,
    query: str,
    task_id: str | None = None,
    threshold: float = 0.2,
    max_items: int = 5,
    max_tokens: int = 1_200,
    embedding_provider: EmbeddingProvider | None = None,
) -> RetrievalResult:
    """Retrieve only currently active approved sections without mutation."""

    query = _text(query, "query", limit=MAX_QUERY_CHARS)
    query_terms = set(_tokens(query))
    normalized_task_id = task_id or f"task-{_digest_text(query)[:16]}"
    normalized_task_id = _text(normalized_task_id, "task_id", limit=MAX_TASK_ID_CHARS)
    if not _SAFE_TASK_ID.fullmatch(normalized_task_id):
        raise RetrievalError("task_id contains unsafe characters")
    threshold = _threshold(threshold)
    max_items = _bounded_int(max_items, "max_items", MAX_ITEMS)
    max_tokens = _bounded_int(max_tokens, "max_tokens", MAX_CONTEXT_TOKENS)
    manifest_revision, assets = _load_approved_assets(approved_root)

    ranked: list[tuple[float, str, str, int, RetrievalContextItem]] = []
    embedding_calls = 0
    embedding_deadline = time.monotonic() + MAX_EMBEDDING_SECONDS if embedding_provider is not None else None
    if max_items > 0 and max_tokens > 0:
        for asset in assets:
            metadata = _metadata_text(asset.frontmatter, asset.asset_key, asset.candidate_id)
            for section in asset.sections:
                lexical = _lexical_score(query_terms, query, metadata, section)
                if embedding_provider is not None:
                    embedding_calls += 1
                    if embedding_calls > MAX_EMBEDDING_CALLS:
                        raise RetrievalError("embedding provider call budget exceeded")
                    remaining = (embedding_deadline or time.monotonic()) - time.monotonic()
                    if remaining <= 0.0:
                        raise RetrievalError("embedding provider time budget exceeded")
                    embedding = _embedding_score(
                        embedding_provider,
                        query,
                        section.text,
                        min(EMBEDDING_TIMEOUT_SECONDS, remaining),
                    )
                else:
                    embedding = None
                score = lexical if embedding is None else 0.75 * lexical + 0.25 * embedding
                if score < threshold:
                    continue
                section_id = "section-" + hashlib.sha256(
                    _canonical(
                        {
                            "asset_key": asset.asset_key,
                            "candidate_digest": asset.candidate_digest,
                            "heading": section.heading,
                            "start_line": section.start_line,
                            "end_line": section.end_line,
                        }
                    ).encode("utf-8")
                ).hexdigest()[:24]
                selection = RetrievalSelection(
                    asset.asset_key,
                    asset.candidate_id,
                    asset.candidate_digest,
                    asset.candidate_provenance_digest,
                    asset.approved_provenance_digest,
                    asset.evaluation_id,
                    asset.evaluation_digest,
                    asset.asset_path,
                    section_id,
                    section.heading,
                    section.start_line,
                    section.end_line,
                    score,
                    lexical,
                    embedding,
                    _token_count(section.text),
                    _digest_text(section.text),
                )
                ranked.append(
                    (
                        -score,
                        asset.asset_key,
                        asset.candidate_digest,
                        section.start_line,
                        RetrievalContextItem(selection, section.text),
                    )
                )

    ranked.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4].selection.heading))
    selected: list[RetrievalContextItem] = []
    used_tokens = 0
    for _, _, _, _, item in ranked:
        if len(selected) >= max_items:
            break
        if used_tokens + item.selection.token_count > max_tokens:
            continue
        selected.append(item)
        used_tokens += item.selection.token_count

    created_at = _timestamp()
    query_digest = _digest_text(query)
    record_seed = {
        "schema_version": RETRIEVAL_SCHEMA_VERSION,
        "task_id": normalized_task_id,
        "query_digest": query_digest,
        "manifest_revision": manifest_revision,
        "threshold": threshold,
        "max_items": max_items,
        "max_tokens": max_tokens,
        "selections": [item.selection.to_dict() for item in selected],
        "created_at": created_at,
    }
    record_id = "retrieval-" + hashlib.sha256(_canonical(record_seed).encode("utf-8")).hexdigest()[:24]
    record = RetrievalRecord(
        normalized_task_id,
        query_digest,
        manifest_revision,
        threshold,
        max_items,
        max_tokens,
        tuple(item.selection for item in selected),
        created_at,
        record_id,
    )
    return RetrievalResult(record, tuple(selected))


def default_retrieval_path(root: str | Path, record: RetrievalRecord) -> Path:
    return Path(root) / "data" / "retrieval" / f"{record.id}.json"


def _inferred_protected_roots(path: Path) -> tuple[Path, ...]:
    absolute = Path(os.path.abspath(path))
    for ancestor in (absolute, *absolute.parents):
        if ancestor.name == "skills":
            return tuple(ancestor / name for name in ("active", "candidates", "approved"))
    return ()


def _reject_protected_output(path: Path, protected_roots: tuple[str | Path, ...]) -> None:
    try:
        _reject_link_ancestors(path)
    except SkillAssetError as exc:
        raise RetrievalError("retrieval output path contains a symlink or junction") from exc
    if _is_link(path):
        raise RetrievalError("retrieval output path is a symlink or junction")
    target = Path(os.path.abspath(path)).resolve()
    for root in (*_inferred_protected_roots(path), *protected_roots):
        protected = Path(os.path.abspath(root)).resolve()
        if target == protected or protected in target.parents:
            raise RetrievalError("retrieval output overlaps a protected skill root")


def _write_json(path: str | Path, value: Mapping[str, Any], protected_roots: tuple[str | Path, ...], *, immutable: bool) -> Path:
    target = Path(path)
    _reject_protected_output(target, protected_roots)
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    if target.exists() and immutable:
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise RetrievalError("existing retrieval record cannot be read") from exc
        if existing != encoded:
            raise RetrievalError("retrieval records are immutable")
        return target
    try:
        atomic_write_text(target, encoded)
    except (LedgerError, OSError) as exc:
        raise RetrievalError("retrieval output cannot be written") from exc
    return target


def write_retrieval_record(
    path: str | Path,
    record: RetrievalRecord,
    *,
    protected_roots: tuple[str | Path, ...] = (),
) -> Path:
    """Persist attribution metadata without storing the query or raw task data."""

    return _write_json(path, record.to_dict(), protected_roots, immutable=True)


def write_retrieval_context(
    path: str | Path,
    result: RetrievalResult,
    *,
    protected_roots: tuple[str | Path, ...] = (),
) -> Path:
    """Write a bounded context-injection preview/export."""

    return _write_json(path, result.to_dict(), protected_roots, immutable=False)


__all__ = [
    "EmbeddingProvider",
    "RetrievalContextItem",
    "RetrievalError",
    "RetrievalRecord",
    "RetrievalResult",
    "RetrievalSelection",
    "default_retrieval_path",
    "retrieve_approved_assets",
    "write_retrieval_context",
    "write_retrieval_record",
]

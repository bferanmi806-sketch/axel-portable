"""Local, fail-open capture helpers for Codex command hooks.

This module deliberately uses only the Python standard library and the engine's
existing redaction/model code.  It accepts documented Codex hook JSON rather
than inspecting transcript files, which are explicitly not a stable hook API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from axel_improve.models import Trajectory  # noqa: E402
from axel_improve.redaction import Redactor  # noqa: E402


ADAPTER_VERSION = "1"
MAX_DIAGNOSTIC_CHARS = 160
MAX_OUTBOX_CHARS = 240_000
MAX_OUTBOX_EVENTS = 100
STATE_LOCK_WAIT_SECONDS = 0.75
STATE_LOCK_STALE_SECONDS = 60.0
SUPPORTED_EVENTS = frozenset(
    {"SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_id(*parts: object) -> str:
    text = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:32]


def as_text(value: object, fallback: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def project_id(cwd: object) -> str:
    return f"project-{stable_id(as_text(cwd, 'unknown-cwd'))[:16]}"


def diagnostic(root: Path, reason: str) -> None:
    """Write only a bounded, content-free local diagnostic."""

    try:
        directory = root / "spool"
        directory.mkdir(parents=True, exist_ok=True)
        entry = {"created_at": utc_now(), "adapter": "codex-hooks", "reason": reason[:MAX_DIAGNOSTIC_CHARS]}
        with (directory / "diagnostics.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")
    except OSError:
        pass


@dataclass
class Segment:
    session_id: str
    project_id: str
    task: str = "Host task was not captured"
    host_version: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    event_ids: set[str] = field(default_factory=set)
    started_at: str = field(default_factory=utc_now)

    def add(self, event: dict[str, Any]) -> None:
        event_id = event["id"]
        if event_id not in self.event_ids:
            self.event_ids.add(event_id)
            self.events.append(event)

    def envelope(self, reason: str) -> dict[str, Any]:
        record_id = f"codex-{stable_id(self.session_id, self.started_at, *(item['id'] for item in self.events))}"
        return {
            "schema_version": 1,
            "id": record_id,
            "host": "codex",
            "host_version": self.host_version,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "task": self.task,
            "events": self.events,
            "outcome": {
                "status": "unevaluated",
                "summary": f"Codex capture segment ended at {reason}.",
                "evaluation": {"status": "unevaluated", "validators": []},
            },
            "metadata": {"adapter": "codex-hooks", "adapter_version": ADAPTER_VERSION},
            "created_at": self.started_at,
        }


def state_path(root: Path, session_id: str) -> Path:
    return root / "spool" / "state" / f"codex-{stable_id(session_id)}.json"


@contextmanager
def state_lock(root: Path, session_id: str):
    """Serialize load/modify/save across independently spawned hook processes."""

    lock = state_path(root, session_id).with_suffix(".lock")
    acquired = False
    descriptor: int | None = None
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + STATE_LOCK_WAIT_SECONDS
        while True:
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                    stream.write(str(os.getpid()))
                descriptor = None
                acquired = True
                yield True
                return
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > STATE_LOCK_STALE_SECONDS:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    diagnostic(root, "capture state lock was unavailable")
                    yield False
                    return
                time.sleep(0.01)
            except OSError:
                diagnostic(root, "capture state lock could not be created")
                yield False
                return
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if acquired:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                diagnostic(root, "capture state lock could not be removed")


def load_segment(root: Path, session_id: str, payload: Mapping[str, Any]) -> Segment:
    path = state_path(root, session_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            events = raw.get("events", [])
            if isinstance(events, list) and all(isinstance(item, dict) for item in events):
                return Segment(
                    session_id=session_id,
                    project_id=as_text(raw.get("project_id"), project_id(payload.get("cwd"))),
                    task=as_text(raw.get("task"), "Host task was not captured"),
                    host_version=as_text(raw.get("host_version")) or None,
                    events=events,
                    event_ids={as_text(item.get("id")) for item in events},
                    started_at=as_text(raw.get("started_at"), utc_now()),
                )
    except (OSError, ValueError, TypeError):
        diagnostic(root, "capture state was unavailable or malformed")
    return Segment(
        session_id=session_id,
        project_id=project_id(payload.get("cwd")),
        host_version=as_text(payload.get("codex_version") or os.environ.get("CODEX_VERSION"), "unknown"),
    )


def save_segment(root: Path, segment: Segment) -> bool:
    try:
        path = state_path(root, segment.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "project_id": segment.project_id,
            "task": segment.task,
            "host_version": segment.host_version,
            "events": segment.events,
            "started_at": segment.started_at,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
        return True
    except OSError:
        diagnostic(root, "capture state could not be written")
        return False


def remove_state(root: Path, session_id: str) -> None:
    try:
        state_path(root, session_id).unlink(missing_ok=True)
    except OSError:
        diagnostic(root, "capture state could not be removed")


def sanitize_event(value: Mapping[str, Any]) -> dict[str, Any]:
    """Use the existing deterministic redactor before adapter-side persistence."""

    redactor = Redactor.from_environment()
    sanitized = redactor.sanitize(dict(value))
    return sanitized if isinstance(sanitized, dict) else {}


def pending_directory(root: Path, session_id: str) -> Path:
    return root / "spool" / "pending" / f"codex-{stable_id(session_id)}"


def queue_pending(root: Path, payload: Mapping[str, Any]) -> bool:
    """Persist a sanitized hook payload when its session state is locked."""

    session_id = as_text(payload.get("session_id"))
    if not session_id:
        return False
    safe_payload = sanitize_event(payload)
    directory = pending_directory(root, session_id)
    token = stable_id(session_id, payload.get("hook_event_name"), payload.get("turn_id"), time.time_ns(), os.getpid())
    path = directory / f"{time.time_ns()}-{token}.json"
    temporary = path.with_suffix(".tmp")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"payload": safe_payload}, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        return True
    except OSError:
        diagnostic(root, "pending hook payload could not be written")
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def load_pending(root: Path, session_id: str) -> list[tuple[Path, dict[str, Any]]]:
    directory = pending_directory(root, session_id)
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return []
    pending: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            payload = raw.get("payload", raw) if isinstance(raw, dict) else None
            if isinstance(payload, dict):
                pending.append((path, payload))
            else:
                path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            diagnostic(root, "pending hook payload was malformed")
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return pending


def remove_pending(paths: list[tuple[Path, dict[str, Any]]]) -> None:
    for path, _ in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def add_payload(segment: Segment, payload: Mapping[str, Any], session_id: str) -> None:
    item = event_for(payload, session_id)
    if item is not None:
        segment.add(sanitize_event(item))
        if as_text(payload.get("hook_event_name")) == "UserPromptSubmit":
            segment.task = as_text(
                sanitize_event({"prompt": payload.get("prompt")}).get("prompt"),
                "Host task was not captured",
            )


def event_for(payload: Mapping[str, Any], session_id: str) -> dict[str, Any] | None:
    name = as_text(payload.get("hook_event_name"))
    turn_id = as_text(payload.get("turn_id"), "session")
    if name == "UserPromptSubmit":
        prompt = as_text(payload.get("prompt"))
        if not prompt:
            return None
        return {
            "id": f"codex-{stable_id(session_id, turn_id, name)}",
            "type": "user_prompt",
            "input": {"prompt": prompt},
            "created_at": utc_now(),
        }
    if name == "PreToolUse":
        tool_id = as_text(payload.get("tool_use_id"), stable_id(turn_id, payload.get("tool_name")))
        return {
            "id": f"codex-{stable_id(session_id, tool_id, 'call')}",
            "type": "tool_call",
            "tool_name": as_text(payload.get("tool_name"), "unknown"),
            "input": payload.get("tool_input"),
            "status": "started",
            "created_at": utc_now(),
        }
    if name == "PostToolUse":
        tool_id = as_text(payload.get("tool_use_id"), stable_id(turn_id, payload.get("tool_name")))
        return {
            "id": f"codex-{stable_id(session_id, tool_id, 'result')}",
            "type": "tool_result",
            "tool_name": as_text(payload.get("tool_name"), "unknown"),
            "input": payload.get("tool_input"),
            "output": payload.get("tool_response"),
            "status": "completed",
            "created_at": utc_now(),
        }
    if name == "Stop":
        message = as_text(payload.get("last_assistant_message"))
        if not message:
            return None
        return {
            "id": f"codex-{stable_id(session_id, turn_id, name)}",
            "type": "assistant_message",
            "output": {"message": message},
            "created_at": utc_now(),
        }
    return None


def fit_trajectory(trajectory: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Keep the serialized outbox below the engine's JSONL line limit."""

    events = trajectory.get("events", [])
    if len(events) > MAX_OUTBOX_EVENTS:
        trajectory = {
            **trajectory,
            "events": [*events[: MAX_OUTBOX_EVENTS - 1], events[-1]],
            "metadata": {
                **trajectory.get("metadata", {}),
                "capture_truncated": "events reduced to preserve the terminal event",
            },
        }
    rendered = json.dumps(trajectory, ensure_ascii=True, sort_keys=True)
    if len(rendered) <= MAX_OUTBOX_CHARS:
        return trajectory, rendered
    compacted = {
        **trajectory,
        "events": [
            {
                "id": event.get("id"),
                "type": event.get("type"),
                "tool_name": event.get("tool_name"),
                "input": "[TRUNCATED:envelope size]",
                "output": "[TRUNCATED:envelope size]",
                "status": event.get("status"),
                "created_at": event.get("created_at"),
            }
            for event in trajectory["events"]
        ],
        "metadata": {
            **trajectory.get("metadata", {}),
            "capture_truncated": "event payloads reduced to fit envelope size limit",
        },
    }
    rendered = json.dumps(compacted, ensure_ascii=True, sort_keys=True)
    if len(rendered) <= MAX_OUTBOX_CHARS:
        return compacted, rendered
    minimal = {
        **compacted,
        "task": "[TRUNCATED:envelope size]",
        "events": [
            {
                "id": event.get("id"),
                "type": event.get("type"),
                "tool_name": event.get("tool_name"),
                "status": event.get("status"),
                "created_at": event.get("created_at"),
            }
            for event in compacted["events"]
        ],
    }
    minimal_rendered = json.dumps(minimal, ensure_ascii=True, sort_keys=True)
    if len(minimal_rendered) > MAX_OUTBOX_CHARS:
        raise ValueError("capture envelope remains above the size limit after compaction")
    return minimal, minimal_rendered


def spool_segment(root: Path, segment: Segment, reason: str) -> Path | None:
    """Validate and durably write one complete local JSONL envelope."""

    try:
        trajectory = Trajectory.from_mapping(segment.envelope(reason)).to_dict()
        trajectory, rendered = fit_trajectory(trajectory)
    except Exception:
        diagnostic(root, "capture envelope was rejected")
        return None
    outbox = root / "spool" / "outbox" / f"{trajectory['id']}.jsonl"
    temporary = outbox.with_name(f".{outbox.name}.tmp")
    try:
        outbox.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(outbox)
    except OSError:
        diagnostic(root, "capture spool was unavailable")
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return outbox


def deliver_outbox(root: Path, outbox: Path) -> None:
    """Try bounded delivery without deleting an unconfirmed outbox."""

    command_text = os.environ.get("AXEL_IMPROVE_COMMAND", "axel-improve")
    try:
        command = [part.strip('"') for part in shlex.split(command_text, posix=os.name != "nt")]
        if not command:
            raise ValueError("empty command")
        result = subprocess.run(
            [*command, "ingest", "--root", str(root), "--input", str(outbox)],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        if result.returncode == 0:
            outbox.unlink(missing_ok=True)
        else:
            diagnostic(root, "engine delivery was unavailable")
    except (OSError, ValueError, subprocess.SubprocessError):
        diagnostic(root, "engine delivery was unavailable")


def spool_and_deliver(root: Path, segment: Segment, reason: str) -> bool:
    """Validate, write a complete local JSONL envelope, then try bounded delivery."""

    outbox = spool_segment(root, segment, reason)
    if outbox is None:
        return False
    deliver_outbox(root, outbox)
    return True


def process(payload: Mapping[str, Any], root: Path) -> None:
    """Process one documented hook payload. All failures are deliberately contained."""

    name = as_text(payload.get("hook_event_name"))
    session_id = as_text(payload.get("session_id"))
    if name not in SUPPORTED_EVENTS or not session_id:
        diagnostic(root, "unsupported or malformed hook payload")
        return
    pending_delivery: Path | None = None
    with state_lock(root, session_id) as locked:
        if not locked:
            queue_pending(root, payload)
            return
        segment = load_segment(root, session_id, payload)
        pending = load_pending(root, session_id)
        for _, pending_payload in pending:
            add_payload(segment, pending_payload, session_id)
        add_payload(segment, payload, session_id)
        if name in {"Stop", "SessionEnd"}:
            if segment.events:
                pending_delivery = spool_segment(root, segment, name)
            if pending_delivery is not None or not segment.events:
                remove_state(root, session_id)
                remove_pending(pending)
            elif not save_segment(root, segment):
                queue_pending(root, payload)
        else:
            if save_segment(root, segment):
                remove_pending(pending)
            else:
                queue_pending(root, payload)
    if pending_delivery is not None:
        deliver_outbox(root, pending_delivery)

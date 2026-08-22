"""Deterministic redaction and bounding for untrusted trajectory input."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import UnsafePathError


@dataclass(frozen=True)
class RedactionConfig:
    """Limits applied before a trajectory is validated or persisted."""

    max_string_chars: int = 4096
    max_output_chars: int = 2048
    max_collection_items: int = 100
    max_depth: int = 8


@dataclass
class RedactionStats:
    """Counters describing what the redactor changed."""

    redacted: int = 0
    truncated: int = 0
    hashed_paths: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "redacted": self.redacted,
            "truncated": self.truncated,
            "hashed_paths": self.hashed_paths,
        }


_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|password|passwd|"
    r"secret|cookie|private[_-]?key|credential|refresh[_-]?token|session[_-]?token|"
    r"(?:^|[_-])token(?:$|[_-]))",
    re.IGNORECASE,
)
_OUTPUT_KEY = re.compile(
    r"(?:output|stdout|stderr|result|diff|traceback|content|body|response)",
    re.IGNORECASE,
)
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_COOKIE_HEADER = re.compile(r"\b(?:Cookie|Set-Cookie)\s*:\s*[^\r\n]+", re.IGNORECASE)
_AUTHORIZATION_HEADER = re.compile(r"\b(?:Proxy-)?Authorization\s*:\s*[^\r\n]+", re.IGNORECASE)
_COOKIE_ASSIGNMENT = re.compile(
    r"\b(?:sessionid|session_id|sid|csrftoken|xsrf[-_]?token|auth[-_]?token|"
    r"refresh[-_]?token|cookie)\s*=\s*[^\s,;]+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+\-/=]+", re.IGNORECASE)
_TOKEN_PREFIX = re.compile(
    r"\b(?:sk|gh[pousr]?|github_pat|xox[baprs]|AIza)[_-][A-Za-z0-9._\-]{8,}\b|"
    r"\bAKIA[0-9A-Z]{16}\b",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_NAME = (
    r"(?:api[_-]?key|access[_-]?token|authorization|auth|bearer|password|passwd|secret|token|"
    r"credential|private[_-]?key)"
)
_ASSIGNED_SECRET = re.compile(
    r"(?i)(\b(?=[A-Za-z0-9_-]*"
    + _SECRET_ASSIGNMENT_NAME
    + r"[A-Za-z0-9_-]*\"?\s*[:=])[A-Za-z][A-Za-z0-9_-]*\"?\s*[:=]\s*)"
    r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;]+)"
)
_SENSITIVE_PATH = re.compile(
    r"(?:^|[\\/])(?:\.env(?:\.[^\\/]*)?|\.aws[\\/](?:credentials|config)|"
    r"auth(?:entication)?\.json|credentials?(?:[\\/]|$)|[^\\/]*\.(?:pem|key))"
    r"[.,;:)]*$",
    re.IGNORECASE,
)
_ABSOLUTE_PATH_TOKEN = re.compile(r"(?i)(?:[A-Za-z]:[\\/]|\\\\|/)[^\s\"'<>]+")

_SENSITIVE_KEY_STEMS = (
    "apikey",
    "accesstoken",
    "authorization",
    "auth",
    "bearer",
    "password",
    "passwd",
    "secret",
    "cookie",
    "privatekey",
    "credential",
    "refreshtoken",
    "sessiontoken",
    "token",
)
_NON_SECRET_KEY_EXCEPTIONS = frozenset({"tokenbudget", "tokencount", "tokenlimit", "tokenestimate"})
_HEADER_VALUE_KEYS = frozenset({"value", "values", "content", "data"})


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized in _NON_SECRET_KEY_EXCEPTIONS:
        return False
    if any(normalized == stem or normalized.startswith(stem) for stem in _SENSITIVE_KEY_STEMS):
        return True
    return bool(_SENSITIVE_KEY.search(key))


def _is_path_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return (
        normalized in {"path", "file", "cwd", "worktree"}
        or normalized.endswith("path")
        or normalized.endswith("directory")
        or normalized.endswith("dir")
    )


def _is_output_key(key: str) -> bool:
    return bool(_OUTPUT_KEY.search(key))


def _path_digest(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"[PATH:{digest}]"


def _contains_traversal(value: str) -> bool:
    return any(part == ".." for part in re.split(r"[\\/]", value))


def reject_unsafe_paths(value: Any, key_hint: str = "") -> None:
    """Reject path traversal and NUL bytes before sanitization.

    The check is limited to path-like fields so ordinary task text can mention
    a relative-dot example without being rejected as a filesystem operation.
    """

    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            next_hint = key_text if _is_path_key(key_text) else key_hint
            reject_unsafe_paths(child, next_hint)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            reject_unsafe_paths(child, key_hint)
        return
    if not isinstance(value, str):
        return
    if "\x00" in value:
        raise UnsafePathError("NUL byte is not allowed in a path field")
    if _is_path_key(key_hint) and _contains_traversal(value):
        raise UnsafePathError("path traversal is not allowed in a path field")


@dataclass
class Redactor:
    """Sanitize nested JSON-compatible values without executing them."""

    config: RedactionConfig = field(default_factory=RedactionConfig)
    secret_values: tuple[str, ...] = ()
    stats: RedactionStats = field(default_factory=RedactionStats)

    @classmethod
    def from_environment(cls, config: RedactionConfig | None = None) -> "Redactor":
        """Load likely secret environment values without persisting them."""

        values: list[str] = []
        for key, value in os.environ.items():
            if value and _is_sensitive_key(key):
                values.append(value)
        return cls(config=config or RedactionConfig(), secret_values=tuple(values))

    def fork(self) -> "Redactor":
        """Create a fresh counter set with the same immutable configuration."""

        return Redactor(config=self.config, secret_values=self.secret_values)

    def sanitize(self, value: Any, key_hint: str = "", depth: int = 0) -> Any:
        """Return a bounded and redacted JSON-compatible copy of ``value``."""

        if depth > self.config.max_depth:
            self.stats.truncated += 1
            return "[TRUNCATED:maximum nesting depth]"

        if key_hint and _is_sensitive_key(key_hint):
            self.stats.redacted += 1
            return "[REDACTED:sensitive field]"

        if isinstance(value, Mapping):
            header_name = value.get("name", value.get("key", value.get("header")))
            if isinstance(header_name, str) and _is_sensitive_key(header_name):
                result: dict[str, Any] = {}
                for key, child in value.items():
                    key_text = str(key)
                    safe_key = self._sanitize_key(key_text)
                    if key_text.lower() in _HEADER_VALUE_KEYS:
                        self.stats.redacted += 1
                        result[safe_key] = "[REDACTED:sensitive field]"
                    else:
                        result[safe_key] = self.sanitize(child, key_text, depth + 1)
                return result
            items = list(value.items())
            collection_truncated = False
            if len(items) > self.config.max_collection_items:
                self.stats.truncated += 1
                items = items[: max(0, self.config.max_collection_items - 1)]
                collection_truncated = True
            result = {
                self._sanitize_key(str(key)): self.sanitize(child, str(key), depth + 1)
                for key, child in items
            }
            if collection_truncated:
                result["[TRUNCATED:collection]"] = "[TRUNCATED:maximum collection items]"
            return result

        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            items = list(value)
            collection_truncated = False
            if len(items) > self.config.max_collection_items:
                self.stats.truncated += 1
                items = items[: max(0, self.config.max_collection_items - 1)]
                collection_truncated = True
            result = [self.sanitize(child, key_hint, depth + 1) for child in items]
            if collection_truncated:
                result.append("[TRUNCATED:maximum collection items]")
            return result

        if isinstance(value, (str, bytes, bytearray)):
            text = value.decode("utf-8", errors="replace") if not isinstance(value, str) else value
            return self._sanitize_string(text, key_hint)

        if value is None or isinstance(value, (bool, int)):
            return value

        if isinstance(value, float):
            if math.isfinite(value):
                return value
            self.stats.redacted += 1
            return "[REDACTED:non-finite number]"

        self.stats.redacted += 1
        return "[REDACTED:unsupported value]"

    def _sanitize_string(self, value: str, key_hint: str) -> str:
        text = value
        for secret in sorted(self.secret_values, key=len, reverse=True):
            if secret and secret in text:
                text = text.replace(secret, "[REDACTED:environment secret]")
                self.stats.redacted += 1

        for pattern, replacement in (
            (_PRIVATE_KEY, "[REDACTED:private key]"),
            (_COOKIE_HEADER, "[REDACTED:cookie header]"),
            (_AUTHORIZATION_HEADER, "[REDACTED:authorization header]"),
            (_COOKIE_ASSIGNMENT, "[REDACTED:cookie value]"),
            (_BEARER, "Bearer [REDACTED:token]"),
            (_TOKEN_PREFIX, "[REDACTED:token]"),
        ):
            text, count = pattern.subn(replacement, text)
            self.stats.redacted += count

        text, count = _ASSIGNED_SECRET.subn(r"\1[REDACTED:assigned secret]", text)
        self.stats.redacted += count
        text = self._redact_inline_credential_paths(text)

        if _is_path_key(key_hint):
            if _SENSITIVE_PATH.search(text):
                self.stats.redacted += 1
                return "[REDACTED:credential path]"
            if _ABSOLUTE_PATH.match(text):
                self.stats.hashed_paths += 1
                return _path_digest(text)

        limit = self.config.max_output_chars if _is_output_key(key_hint) else self.config.max_string_chars
        if len(text) > limit:
            self.stats.truncated += 1
            return f"{text[:limit]} [TRUNCATED:{len(text) - limit} chars]"
        return text

    def _sanitize_key(self, key: str) -> str:
        """Bound keys and redact keys that themselves contain secret material."""

        if (
            _PRIVATE_KEY.search(key)
            or _COOKIE_HEADER.search(key)
            or _AUTHORIZATION_HEADER.search(key)
            or _COOKIE_ASSIGNMENT.search(key)
            or _ASSIGNED_SECRET.search(key)
            or _TOKEN_PREFIX.search(key)
            or _SENSITIVE_PATH.search(key)
            or _ABSOLUTE_PATH_TOKEN.search(key)
        ):
            self.stats.redacted += 1
            return "[REDACTED:key]"
        if len(key) > self.config.max_string_chars:
            self.stats.truncated += 1
            return f"{key[:self.config.max_string_chars]} [TRUNCATED:key]"
        return key

    def _redact_inline_credential_paths(self, text: str) -> str:
        """Redact credential-bearing absolute paths embedded in free text."""

        def replace(match: re.Match[str]) -> str:
            token = match.group(0)
            clean = token.rstrip(".,;:)]}")
            punctuation = token[len(clean) :]
            if _SENSITIVE_PATH.search(clean):
                self.stats.redacted += 1
                return "[REDACTED:credential path]" + punctuation
            return token

        return _ABSOLUTE_PATH_TOKEN.sub(replace, text)

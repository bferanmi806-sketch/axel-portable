"""Read-only discovery of versioned skill assets.

The scanner deliberately treats every directory as untrusted input.  It never
follows symlinks and it only recognises an asset named ``SKILL.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from collections.abc import Mapping
from typing import Any


class SkillAssetError(ValueError):
    """Raised when a skill asset cannot safely be interpreted."""


def _is_link(path: Path) -> bool:
    junction = getattr(path, "is_junction", None)
    if path.is_symlink() or bool(junction and junction()):
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        attributes = 0
    return bool(attributes & 0x400)


def _reject_link_ancestors(path: Path) -> None:
    current = Path(os.path.abspath(path))
    while True:
        if _is_link(current):
            raise SkillAssetError("skill path contains a symlink or junction")
        if current.parent == current:
            return
        current = current.parent


def content_digest(content: str | bytes) -> str:
    """Return the SHA-256 digest for exact UTF-8 skill content."""

    payload = content if isinstance(content, bytes) else content.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_digest(path: Path) -> str:
    """Return the digest of the exact bytes currently stored on disk."""

    return content_digest(read_bounded(path, 131_072))


def read_bounded(path: Path, limit: int) -> bytes:
    """Read at most one byte beyond a trusted size boundary."""

    with path.open("rb") as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise SkillAssetError("skill asset exceeds size limit")
    return content


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse the deliberately small, non-executing frontmatter subset.

    Candidate metadata is emitted by this package, so accepting a broad YAML
    grammar would add parsing ambiguity without providing a useful capability.
    Values are strings or bracketed comma-separated string lists only.
    """

    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, content
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise SkillAssetError("unterminated skill frontmatter")

    parsed: dict[str, Any] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise SkillAssetError("invalid skill frontmatter line")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", key):
            raise SkillAssetError("unsafe skill frontmatter key")
        if value.startswith("[") and value.endswith("]"):
            parsed[key] = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        else:
            parsed[key] = value
    return parsed, "".join(lines[end + 1 :])


@dataclass(frozen=True)
class SkillAsset:
    """A discovered skill, represented without mutating its source tree."""

    state: str
    root: Path
    relative_path: Path
    content: str
    frontmatter: Mapping[str, Any]
    raw_content: bytes | None = None

    @property
    def path(self) -> Path:
        return self.root / self.relative_path

    @property
    def digest(self) -> str:
        return content_digest(self.raw_content if self.raw_content is not None else self.content)

    @property
    def name(self) -> str:
        value = self.frontmatter.get("name")
        if isinstance(value, str) and value:
            return value
        return self.relative_path.parent.name


class SkillBank:
    """Read-only inventory of active, candidate, and approved skill assets."""

    def __init__(
        self,
        active_root: Path | str,
        candidate_root: Path | str | None = None,
        approved_root: Path | str | None = None,
        *,
        max_asset_bytes: int = 131_072,
    ) -> None:
        self.active_root = Path(active_root)
        self.candidate_root = Path(candidate_root) if candidate_root is not None else None
        self.approved_root = Path(approved_root) if approved_root is not None else None
        self.max_asset_bytes = max_asset_bytes

    def scan(self) -> tuple[SkillAsset, ...]:
        assets: list[SkillAsset] = []
        for state, root in (
            ("active", self.active_root),
            ("candidate", self.candidate_root),
            ("approved", self.approved_root),
        ):
            if root is not None:
                assets.extend(self._scan_root(state, root))
        return tuple(sorted(assets, key=lambda asset: (asset.state, asset.relative_path.as_posix())))

    def active_assets(self) -> tuple[SkillAsset, ...]:
        return tuple(asset for asset in self.scan() if asset.state == "active")

    def resolve_active_target(self, target: str) -> SkillAsset | None:
        """Resolve an explicit active target by name or safe relative path."""

        if not isinstance(target, str) or not target.strip():
            raise SkillAssetError("target must be a non-empty string")
        requested = target.strip().replace("\\", "/").rstrip("/")
        if requested.startswith("/") or ".." in requested.split("/"):
            raise SkillAssetError("unsafe target path")
        matches = [
            asset
            for asset in self.active_assets()
            if requested in {
                asset.name,
                asset.relative_path.as_posix(),
                asset.relative_path.parent.as_posix(),
            }
        ]
        if len(matches) > 1:
            raise SkillAssetError("target is ambiguous")
        return matches[0] if matches else None

    def _scan_root(self, state: str, root: Path) -> list[SkillAsset]:
        _reject_link_ancestors(root)
        if not root.exists():
            return []
        if _is_link(root) or not root.is_dir():
            raise SkillAssetError("skill root must be a real directory")
        root = root.resolve()
        assets: list[SkillAsset] = []
        for skill_path in root.rglob("SKILL.md"):
            parent = skill_path.parent
            has_symlink_parent = False
            has_hidden_parent = False
            while parent != root:
                if _is_link(parent):
                    has_symlink_parent = True
                    break
                if parent.name.startswith("."):
                    has_hidden_parent = True
                    break
                parent = parent.parent
            if _is_link(skill_path) or has_symlink_parent or has_hidden_parent or not skill_path.is_file():
                continue
            try:
                relative_path = skill_path.relative_to(root)
            except ValueError:
                continue
            raw_content = read_bounded(skill_path, self.max_asset_bytes)
            try:
                content = raw_content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SkillAssetError("skill asset is not valid UTF-8") from exc
            frontmatter, _ = parse_frontmatter(content)
            assets.append(SkillAsset(state, root, relative_path, content, frontmatter, raw_content))
        return assets

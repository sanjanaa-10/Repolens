"""Safe filesystem discovery for cloned repositories.

Every repository is treated as untrusted input. This module:
  * walks the working tree without following symlinked directories,
  * refuses to descend into ignored/generated trees,
  * verifies every discovered path stays inside the repository root,
  * rejects paths that resolve outside the repository root (symlink escape),
  * exposes a containment-checked join helper for later phases to read files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.repositories.errors import InternalIngestionError
from app.repositories.ignore import should_ignore_directory, should_ignore_file
from app.repositories.languages import detect_language


class PathEscapeError(ValueError):
    """Raised when a path resolves outside the repository root."""


@dataclass
class DiscoveredFile:
    """Metadata for one discovered file, all paths relative to the root."""

    relative_path: str
    language: str | None
    size_bytes: int
    line_count: int = 0
    analyzable: bool = False

    @property
    def extension(self) -> str:
        return Path(self.relative_path).suffix


@dataclass
class DiscoveryResult:
    files: list[DiscoveredFile] = field(default_factory=list)
    total_size_bytes: int = 0

    def by_language_bytes(self) -> dict[str, int]:
        """Total bytes grouped by supported language."""
        totals: dict[str, int] = {}
        for f in self.files:
            if f.language:
                totals[f.language] = totals.get(f.language, 0) + f.size_bytes
        return totals


def _resolve_inside(root: Path, node: Path) -> Path | None:
    """Return the real, resolved path of ``node`` if it stays under ``root``."""
    try:
        resolved = node.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def safe_relative_path(root: Path, node: Path) -> str:
    """Return ``node`` as a forward-slash relative path after containment check."""
    root_resolved = root.resolve()
    try:
        rel = node.resolve(strict=False).relative_to(root_resolved)
    except (ValueError, OSError):
        raise PathEscapeError(
            f"Path escaped repository root: {node}"
        ) from None
    return rel.as_posix()


def safe_join(root: Path, relative_path: str) -> Path:
    """Join a client-supplied relative path to the root, blocking escapes.

    Rejects absolute paths, '..' traversal, and any result that would resolve
    outside the repository root. Used by later phases to read repository files.
    """
    root_resolved = Path(root).resolve()
    rel = relative_path.replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if rel.startswith("/") or not parts:
        raise PathEscapeError(f"Invalid relative path: {relative_path!r}")
    if any(p == ".." or "\x00" in p or ":" in p for p in parts):
        raise PathEscapeError(f"Path traversal detected: {relative_path!r}")

    candidate = root_resolved.joinpath(*parts)
    resolved = _resolve_inside(root_resolved, candidate)
    if resolved is None:
        raise PathEscapeError(f"Path escapes repository root: {relative_path!r}")
    return candidate


def _is_symlink_escape(root: Path, node: Path) -> bool:
    """True if ``node`` is a symlink resolving outside ``root``."""
    if not node.is_symlink():
        return False
    try:
        node.resolve(strict=False).relative_to(root.resolve())
    except (ValueError, OSError):
        return True
    return False


def _count_lines(path: Path, size_bytes: int, max_size: int) -> int:
    """Count lines for reasonably-sized files; 0 for oversized ones."""
    if size_bytes > max_size:
        return 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def discover_files(
    root: Path,
    *,
    max_file_size_bytes: int,
    max_files: int,
    max_repo_bytes: int,
) -> DiscoveryResult:
    """Walk a cloned repository and return safe file metadata.

    Raises an ingestion error if the repository exceeds configured limits.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise InternalIngestionError("Repository working tree is missing.")

    result = DiscoveryResult()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames if not should_ignore_directory(d)
        )
        base = Path(dirpath)

        for filename in filenames:
            if should_ignore_file(filename):
                continue

            node = base / filename
            if _is_symlink_escape(root, node):
                continue

            try:
                stat = node.stat()
            except OSError:
                continue

            rel = safe_relative_path(root, node)
            result.total_size_bytes += stat.st_size

            if len(result.files) >= max_files:
                from app.repositories.errors import TooManyFiles

                raise TooManyFiles()

            if result.total_size_bytes > max_repo_bytes:
                from app.repositories.errors import RepositoryTooLarge

                raise RepositoryTooLarge()

            language = detect_language(rel)
            line_count = _count_lines(node, stat.st_size, max_file_size_bytes)
            analyzable = language is not None and stat.st_size <= max_file_size_bytes

            result.files.append(
                DiscoveredFile(
                    relative_path=rel,
                    language=language,
                    size_bytes=stat.st_size,
                    line_count=line_count,
                    analyzable=analyzable,
                )
            )

    return result
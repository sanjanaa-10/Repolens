"""Repository-scoped source-text search.

Streams the actual stored source of each repository file one file at a time
(never the whole repository at once) and collects line-level matches. Bounded
by ``MAX_TEXT_SCAN_BYTES`` so a single request cannot consume unbounded I/O.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.search import limits
from app.models.orm import FileRecord, Repository
from app.models.schemas import TextSearchHit
from app.repositories.discovery import PathEscapeError, safe_join


def _read_source_file(root: Path, rel_path: str, max_size: int) -> bytes:
    """Read one repository file's bytes, blocking path escapes and oversize."""
    full = safe_join(root, rel_path)
    if full.stat().st_size > max_size:
        return b""
    return full.read_bytes()


async def search_text(
    db: AsyncSession,
    repository: Repository,
    query: str,
    case_sensitive: bool,
    limit: int,
    offset: int,
    files: list[FileRecord] | None = None,
) -> list[TextSearchHit]:
    """Return text matches across repository files ordered by file path.

    Files are visited in ascending path order and scanned one at a time. The
    scan stops once ``MAX_TEXT_SCAN_BYTES`` have been read or
    ``MAX_TEXT_COLLECT`` matches have been gathered.
    """
    if files is None:
        files = (
            (
                await db.execute(
                    select(FileRecord)
                    .where(FileRecord.repository_id == repository.id)
                    .order_by(FileRecord.path)
                )
            )
            .scalars()
            .all()
        )

    root = Path(repository.local_path) if repository.local_path else None
    if root is None or not root.is_dir():
        return []

    if case_sensitive:
        needle = query
    else:
        needle = query.casefold()

    collected: list[TextSearchHit] = []
    scanned = 0

    for file in files:
        if scanned >= limits.MAX_TEXT_SCAN_BYTES or len(collected) >= limits.MAX_TEXT_COLLECT:
            break
        try:
            data = await asyncio.to_thread(
                _read_source_file, root, file.path, limits.MAX_FILE_SOURCE_BYTES
            )
        except (OSError, ValueError, PathEscapeError):
            data = b""
        if not data:
            continue
        scanned += len(data)

        text = data.decode("utf-8", errors="replace")
        for idx, line in enumerate(text.split("\n")):
            if len(collected) >= limits.MAX_TEXT_COLLECT:
                break
            if case_sensitive:
                position_in = needle in line
            else:
                position_in = needle in line.casefold()
            if position_in:
                collected.append(
                    TextSearchHit(
                        file_id=file.id,
                        path=file.path,
                        line_number=idx + 1,
                        snippet=line[: limits.MAX_SNIPPET_LENGTH],
                    )
                )

    return collected[offset : offset + limit]
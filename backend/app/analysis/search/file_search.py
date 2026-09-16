"""Repository-scoped file search.

Matches repository-relative paths / file names against the ``files`` table
using a repository-scoped candidate query, then ranks deterministically so
exact path matches and directory prefixes sort ahead of loose substrings.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.search import limits
from app.models.orm import FileRecord, Symbol
from app.models.schemas import FileSearchHit


def _file_score(path: str, query: str) -> int:
    normalized = path.replace("\\", "/")
    q = query.replace("\\", "/").rstrip("/")
    if not q:
        return 0
    if normalized == q:
        return 6
    if normalized.startswith(q.rstrip("/") + "/"):
        return 5
    if normalized.endswith("/" + q) or normalized == q + "":
        return 4
    if q in normalized:
        return 2
    return 0


async def search_files(
    db: AsyncSession,
    repository_id: int,
    query: str,
    limit: int,
    offset: int,
) -> list[FileSearchHit]:
    """Return ranked file matches for a repository-scoped query."""
    lower = f"%{query.lower()}%"

    symbol_count = (
        select(func.count()).select_from(Symbol).where(Symbol.file_id == FileRecord.id)
    ).scalar_subquery()

    base = (
        select(FileRecord, symbol_count.label("symbol_count"))
        .where(FileRecord.repository_id == repository_id)
        .where(FileRecord.path.ilike(lower))
        .order_by(FileRecord.id)
        .limit(limits.MAX_FILE_CANDIDATES)
    )
    rows = (await db.execute(base)).all()

    ranked = sorted(
        rows,
        key=lambda r: (
            _file_score(r.FileRecord.path, query),
            r.FileRecord.path.replace("\\", "/"),
            r.FileRecord.id,
        ),
        reverse=True,
    )
    window = ranked[offset : offset + limit]

    hits: list[FileSearchHit] = []
    for row in window:
        f = row.FileRecord
        hits.append(
            FileSearchHit(
                file_id=f.id,
                path=f.path,
                language=f.language,
                size_bytes=f.size_bytes,
                line_count=f.line_count,
                symbol_count=int(row.symbol_count or 0),
            )
        )
    return hits
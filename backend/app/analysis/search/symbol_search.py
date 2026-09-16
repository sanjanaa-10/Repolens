"""Repository-scoped symbol search.

Searches the stored ``symbols`` table (name, qualified name) rather than raw
text. Uses a repository-scoped candidate query (kept under
``MAX_SYMBOL_CANDIDATES``) then ranks deterministically in memory with
``ranking.finalize_rank``.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.search import limits
from app.analysis.search.ranking import Rankable, finalize_rank
from app.models.orm import FileRecord, Symbol
from app.models.schemas import SymbolSearchHit


async def search_symbols(
    db: AsyncSession,
    repository_id: int,
    query: str,
    limit: int,
    offset: int,
) -> list[SymbolSearchHit]:
    """Return ranked symbol matches for a repository-scoped query."""
    lower = f"%{query.lower()}%"

    base = (
        select(Symbol, FileRecord.path)
        .join(FileRecord, Symbol.file_id == FileRecord.id)
        .where(Symbol.repository_id == repository_id)
        .where(
            (Symbol.name.ilike(lower)) | (Symbol.qualified_name.ilike(lower))
        )
        .order_by(Symbol.id)
        .limit(limits.MAX_SYMBOL_CANDIDATES)
    )
    rows = (await db.execute(base)).all()

    rankables = [
        Rankable(
            key=(symbol.id,),
            name=symbol.name,
            qualified_name=symbol.qualified_name,
            path=path,
            kind=symbol.kind,
        )
        for symbol, path in rows
    ]

    ranked = sorted(rankables, key=lambda r: finalize_rank(r, query), reverse=True)
    window = ranked[offset : offset + limit]

    by_symbol = {row[0].id: row[0] for row in rows}

    hits: list[SymbolSearchHit] = []
    for r in window:
        sym = by_symbol[r.key[0]]
        hits.append(
            SymbolSearchHit(
                symbol_id=sym.id,
                name=sym.name,
                qualified_name=sym.qualified_name,
                kind=sym.kind,
                language=sym.language,
                file_path=r.path,
                line_start=sym.line_start,
                line_end=sym.line_end,
                exported=sym.exported,
                signature=sym.signature,
            )
        )
    return hits
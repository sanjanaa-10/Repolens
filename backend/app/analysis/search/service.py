"""Search service: validates queries and dispatches to the search modes.

Every builder is repository-scoped. Input validation and the security caps
live here so routes stay thin.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.search import limits
from app.analysis.search.file_search import search_files
from app.analysis.search.symbol_search import search_symbols
from app.analysis.search.text_search import search_text
from app.models.orm import Repository


class QueryError(ValueError):
    pass


VALID_TYPES = {"symbol", "file", "text"}


def validate_query(query: str) -> str:
    if not query:
        raise QueryError("Empty search query.")
    stripped = query.strip()
    if not stripped:
        raise QueryError("Empty search query.")
    if stripped == "*":
        raise QueryError("Wildcard searches are not supported.")
    if len(stripped) > limits.MAX_QUERY_LENGTH:
        raise QueryError(
            f"Query too long (max {limits.MAX_QUERY_LENGTH} characters)."
        )
    controls = [c for c in stripped if ord(c) < 32 or c == "\x7f"]
    if controls:
        raise QueryError("Invalid control characters in query.")
    return stripped


async def run_search(
    db: AsyncSession,
    repository: Repository,
    query: str,
    search_type: str,
    case_sensitive: bool,
    limit: int,
    offset: int,
):
    """Execute one search mode for a repository and return hits."""
    if search_type == "symbol":
        return await search_symbols(db, repository.id, query, limit, offset)
    if search_type == "file":
        return await search_files(db, repository.id, query, limit, offset)
    return await search_text(
        db, repository, query, case_sensitive=case_sensitive, limit=limit, offset=offset
    )
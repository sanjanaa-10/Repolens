"""Diff read service: loads persisted diff data into API schemas."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import (
    Diff,
    DiffChangedLine,
    DiffFile,
    DiffHunk,
    DiffSymbol,
)
from app.models.schemas import (
    DiffChangedLineInfo,
    DiffDetail,
    DiffFileInfo,
    DiffHunkInfo,
    DiffInfo,
    DiffSymbolInfo,
)


class DiffNotFoundError(LookupError):
    pass


async def get_diff(db: AsyncSession, repository_id: int, diff_id: int) -> Diff:
    """Return the Diff row, scoped to a repository."""
    result = await db.execute(
        select(Diff).where(
            Diff.id == diff_id,
            Diff.repository_id == repository_id,
        )
    )
    diff = result.scalar_one_or_none()
    if diff is None:
        raise DiffNotFoundError(f"diff {diff_id} not found")
    return diff


async def get_diff_files(db: AsyncSession, diff_id: int) -> list[DiffFile]:
    result = await db.execute(
        select(DiffFile)
        .where(DiffFile.diff_id == diff_id)
        .order_by(DiffFile.path)
    )
    return list(result.scalars().all())


async def get_diff_hunks(db: AsyncSession, diff_file_id: int) -> list[DiffHunk]:
    result = await db.execute(
        select(DiffHunk)
        .where(DiffHunk.diff_file_id == diff_file_id)
        .order_by(DiffHunk.old_start, DiffHunk.new_start)
    )
    return list(result.scalars().all())


async def get_changed_lines(
    db: AsyncSession, diff_hunk_id: int
) -> list[DiffChangedLine]:
    result = await db.execute(
        select(DiffChangedLine)
        .where(DiffChangedLine.diff_hunk_id == diff_hunk_id)
        .order_by(DiffChangedLine.side, DiffChangedLine.line_number)
    )
    return list(result.scalars().all())


async def get_diff_symbols(db: AsyncSession, diff_id: int) -> list[DiffSymbol]:
    result = await db.execute(
        select(DiffSymbol)
        .where(DiffSymbol.diff_id == diff_id)
        .order_by(DiffSymbol.file_path, DiffSymbol.symbol_name)
    )
    return list(result.scalars().all())


async def diff_to_info(diff: Diff) -> DiffInfo:
    return DiffInfo(
        id=diff.id,
        repository_id=diff.repository_id,
        base_revision=diff.base_revision,
        head_revision=diff.head_revision,
        files_changed=diff.files_changed,
        insertions=diff.insertions,
        deletions=diff.deletions,
        symbols_changed=diff.symbols_changed,
        computed_at=diff.computed_at,
    )


async def diff_to_detail(
    db: AsyncSession,
    diff: Diff,
    *,
    include_files: bool = True,
    include_symbols: bool = True,
) -> DiffDetail:
    files: list[DiffFileInfo] = []
    symbols: list[DiffSymbolInfo] = []

    if include_files:
        file_rows = await get_diff_files(db, diff.id)
        symbol_rows = await get_diff_symbols(db, diff.id)
        symbol_by_file: dict[str, dict] = {}
        for s in symbol_rows:
            entry = symbol_by_file.setdefault(s.file_path, {"count": 0, "types": {}})
            entry["count"] += 1
            entry["types"][s.change_type] = entry["types"].get(s.change_type, 0) + 1

        files = [
            DiffFileInfo(
                id=f.id,
                path=f.path,
                status=f.status,
                old_path=f.old_path,
                new_path=f.new_path,
                additions=f.additions,
                deletions=f.deletions,
                binary=f.binary,
                file_category=f.file_category,
                symbols_changed=(
                    symbol_by_file.get(f.path, {}).get("count", 0)
                ),
                symbol_change_types=symbol_by_file.get(f.path, {}).get("types", {}),
            )
            for f in file_rows
        ]

    if include_symbols:
        symbol_rows = await get_diff_symbols(db, diff.id)
        symbols = [
            DiffSymbolInfo(
                id=s.id,
                symbol_id=s.symbol_id,
                file_path=s.file_path,
                symbol_name=s.symbol_name,
                symbol_kind=s.symbol_kind,
                change_type=s.change_type,
                added_lines=s.added_lines,
                deleted_lines=s.deleted_lines,
            )
            for s in symbol_rows
        ]

    return DiffDetail(
        id=diff.id,
        repository_id=diff.repository_id,
        base_revision=diff.base_revision,
        head_revision=diff.head_revision,
        files_changed=diff.files_changed,
        insertions=diff.insertions,
        deletions=diff.deletions,
        symbols_changed=diff.symbols_changed,
        computed_at=diff.computed_at,
        files=files,
        symbols=symbols,
    )


async def diff_file_to_info(diff_file: DiffFile, symbol_count: int = 0) -> DiffFileInfo:
    return DiffFileInfo(
        id=diff_file.id,
        path=diff_file.path,
        status=diff_file.status,
        old_path=diff_file.old_path,
        new_path=diff_file.new_path,
        additions=diff_file.additions,
        deletions=diff_file.deletions,
        binary=diff_file.binary,
        file_category=diff_file.file_category,
        symbols_changed=symbol_count,
    )


async def hunk_to_info(db: AsyncSession, hunk: DiffHunk) -> DiffHunkInfo:
    lines = await get_changed_lines(db, hunk.id)
    text_by_line: dict[tuple[str, int], str] = {
        (side, line_number): text
        for side, line_number, text in _parse_hunk_lines(hunk.content)
    }
    return DiffHunkInfo(
        id=hunk.id,
        header=hunk.header,
        old_start=hunk.old_start,
        old_count=hunk.old_count,
        new_start=hunk.new_start,
        new_count=hunk.new_count,
        lines=[
            DiffChangedLineInfo(
                side=l.side,
                line_number=l.line_number,
                change_type=l.change_type,
                text=text_by_line.get((l.side, l.line_number)),
            )
            for l in lines
        ],
    )


def _parse_hunk_lines(content: str) -> list[tuple[str, int, str]]:
    """Reconstruct ``(side, line_number, text)`` from unified diff text.

    ``content`` is untrusted repository data; extra prefix lines are tolerated
    and never interpreted as instructions.
    """
    parsed: list[tuple[str, int, str]] = []
    old_line = 0
    new_line = 0
    for raw in content.splitlines():
        if not raw:
            continue
        marker = raw[0]
        body = raw[1:]
        if marker == "+":
            new_line += 1
            parsed.append(("NEW", new_line, body))
        elif marker == "-":
            old_line += 1
            parsed.append(("OLD", old_line, body))
        elif marker == " ":
            new_line += 1
            old_line += 1
    return parsed
"""Internal data model produced by the parsers.

These are plain dataclasses (no ORM, no FastAPI) representing the normalized
result of parsing one file. The analysis service maps them into database rows
for ``symbols``, ``imports``, ``exports``, and ``parse_results``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.analysis.kinds import ExportKind, ImportKind, ParserStatus, SymbolKind


@dataclass(slots=True)
class SymbolNode:
    """One extracted symbol with exact source coordinates.

    Coordinates are 1-based lines and 1-based columns; the end position is
    exclusive (one past the final character), matching the host parser.
    """

    name: str
    kind: SymbolKind
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    parent_index: int | None = None  # index into the containing list
    qualified_name: str | None = None
    exported: bool = False
    signature: str | None = None


@dataclass(slots=True)
class ImportNode:
    source: str
    imported_name: str | None
    alias: str | None
    kind: ImportKind
    start_line: int
    end_line: int


@dataclass(slots=True)
class ExportNode:
    name: str
    kind: ExportKind
    start_line: int
    end_line: int


@dataclass(slots=True)
class ParseOutcome:
    """Everything the parser learned about one file, plus its status."""

    status: ParserStatus
    symbols: list[SymbolNode] = field(default_factory=list)
    imports: list[ImportNode] = field(default_factory=list)
    exports: list[ExportNode] = field(default_factory=list)
    error_message: str | None = None
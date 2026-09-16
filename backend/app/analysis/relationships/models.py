"""Internal data model for relationships produced by the relationship engine."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RelationshipEdge:
    """One resolved (or unresolved) relationship between two code entities."""

    type: str  # DEFINES | IMPORTS | EXPORTS | CALLS | REFERENCES | EXTENDS | IMPLEMENTS | TESTS
    resolution_status: str  # RESOLVED | UNRESOLVED | EXTERNAL
    source_symbol_id: int | None = None
    target_symbol_id: int | None = None
    source_type: str = "symbol"  # symbol | file
    target_type: str = "symbol"  # symbol | file | import
    evidence_file_id: int | None = None
    evidence_start_line: int = 0
    evidence_end_line: int = 0
    evidence: str | None = None
    target_file: str | None = None

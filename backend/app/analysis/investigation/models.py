"""Investigation response for a single symbol.

Assembles only the immediately relevant graph neighborhood (depth 1) plus the
symbol's own file provenance and definition line range. Unresolved and external
edges are surfaced explicitly rather than dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InvestigationEdge:
    relationship_id: int
    relation: str  # CALLS | REFERENCES | IMPORTS | EXPORTS | EXTENDS | IMPLEMENTS | TESTS | DEFINES
    source_symbol_id: int | None
    source_name: str | None
    source_file: str | None
    source_line: int
    target_symbol_id: int | None
    target_name: str | None
    target_type: str  # symbol | file | import
    target_file: str | None
    target_line: int | None
    resolution_status: str  # RESOLVED | UNRESOLVED | EXTERNAL
    evidence: str | None


@dataclass
class InvestigationResult:
    symbol: dict
    definition: dict | None
    callers: list[InvestigationEdge] = field(default_factory=list)
    callees: list[InvestigationEdge] = field(default_factory=list)
    references: list[InvestigationEdge] = field(default_factory=list)
    dependencies: list[InvestigationEdge] = field(default_factory=list)
    tests: list[InvestigationEdge] = field(default_factory=list)
    exports: list[InvestigationEdge] = field(default_factory=list)
    import_context: list[InvestigationEdge] = field(default_factory=list)
    unresolved: list[InvestigationEdge] = field(default_factory=list)
    external: list[InvestigationEdge] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)
    source_context: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "definition": self.definition,
            "callers": [e.__dict__ for e in self.callers],
            "callees": [e.__dict__ for e in self.callees],
            "references": [e.__dict__ for e in self.references],
            "dependencies": [e.__dict__ for e in self.dependencies],
            "tests": [e.__dict__ for e in self.tests],
            "exports": [e.__dict__ for e in self.exports],
            "import_context": [e.__dict__ for e in self.import_context],
            "unresolved": [e.__dict__ for e in self.unresolved],
            "external": [e.__dict__ for e in self.external],
            "related_files": self.related_files,
            "source_context": self.source_context,
        }
"""DEFINES relationship resolution.

Creates DEFINES relationships between files and their top-level symbols,
and between classes and their methods/nested symbols.
"""
from __future__ import annotations

import logging

from app.analysis.relationships.index import RelationshipIndex
from app.analysis.relationships.models import RelationshipEdge

logger = logging.getLogger("repolens.relationships.defines")


def resolve_defines(index: RelationshipIndex) -> list[RelationshipEdge]:
    """Create DEFINES relationships for file→symbol and symbol→nested symbol."""
    edges: list[RelationshipEdge] = []

    for file_entry in index.files:
        syms = index.symbols_by_file.get(file_entry.id, [])
        for sym in syms:
            if sym.kind == "IMPORT":
                continue
            if sym.parent_symbol_id is None:
                edges.append(RelationshipEdge(
                    type="DEFINES",
                    resolution_status="RESOLVED",
                    source_symbol_id=None,
                    target_symbol_id=sym.id,
                    source_type="file",
                    target_type="symbol",
                    evidence_file_id=file_entry.id,
                    evidence_start_line=sym.line_start,
                    evidence_end_line=sym.line_end,
                    evidence=f"file defines {sym.name}",
                    target_file=file_entry.path,
                ))

    for sym in index.symbols:
        if sym.parent_symbol_id and sym.kind != "IMPORT":
            parent = index.symbols_by_id.get(sym.parent_symbol_id)
            if parent:
                edges.append(RelationshipEdge(
                    type="DEFINES",
                    resolution_status="RESOLVED",
                    source_symbol_id=parent.id,
                    target_symbol_id=sym.id,
                    source_type="symbol",
                    target_type="symbol",
                    evidence_file_id=sym.file_id,
                    evidence_start_line=sym.line_start,
                    evidence_end_line=sym.line_end,
                    evidence=f"{parent.name} defines {sym.name}",
                    target_file=sym.file_path,
                ))

    return edges

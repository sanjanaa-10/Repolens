"""Export relationship resolution.

Creates EXPORTS relationships for JavaScript/TypeScript exports.
For Python, does not create EXPORTS since Python has no explicit export syntax.
"""
from __future__ import annotations

import logging

from app.analysis.relationships.index import RelationshipIndex
from app.analysis.relationships.models import RelationshipEdge

logger = logging.getLogger("repolens.relationships.exports")


def resolve_exports(index: RelationshipIndex) -> list[RelationshipEdge]:
    """Create EXPORTS relationships from parsed export information."""
    edges: list[RelationshipEdge] = []

    for exp in index.exports:
        if exp.name == "*":
            edges.append(RelationshipEdge(
                type="EXPORTS",
                resolution_status="RESOLVED",
                source_symbol_id=None,
                target_symbol_id=None,
                source_type="file",
                target_type="file",
                evidence_file_id=exp.file_id,
                evidence_start_line=exp.start_line,
                evidence_end_line=exp.end_line,
                evidence=f"export * from module",
                target_file=exp.file_path,
            ))
            continue

        if exp.name == "default":
            file_syms = index.symbols_by_file.get(exp.file_id, [])
            target = None
            for sym in file_syms:
                if sym.exported and sym.kind in ("FUNCTION", "ASYNC_FUNCTION",
                                                  "ARROW_FUNCTION", "CLASS",
                                                  "INTERFACE", "ENUM"):
                    target = sym
                    break

            if target:
                edges.append(RelationshipEdge(
                    type="EXPORTS",
                    resolution_status="RESOLVED",
                    source_symbol_id=None,
                    target_symbol_id=target.id,
                    source_type="file",
                    target_type="symbol",
                    evidence_file_id=exp.file_id,
                    evidence_start_line=exp.start_line,
                    evidence_end_line=exp.end_line,
                    evidence=f"export default {target.name}",
                    target_file=exp.file_path,
                ))
            else:
                edges.append(RelationshipEdge(
                    type="EXPORTS",
                    resolution_status="RESOLVED",
                    source_symbol_id=None,
                    target_symbol_id=None,
                    source_type="file",
                    target_type="symbol",
                    evidence_file_id=exp.file_id,
                    evidence_start_line=exp.start_line,
                    evidence_end_line=exp.end_line,
                    evidence="export default",
                    target_file=exp.file_path,
                ))
            continue

        target_syms = index.find_symbols_by_name(exp.name)
        exported_target = None
        for sym in target_syms:
            if sym.file_id == exp.file_id and sym.exported:
                exported_target = sym
                break
        if exported_target is None:
            for sym in target_syms:
                if sym.file_id == exp.file_id:
                    exported_target = sym
                    break

        if exported_target:
            edges.append(RelationshipEdge(
                type="EXPORTS",
                resolution_status="RESOLVED",
                source_symbol_id=None,
                target_symbol_id=exported_target.id,
                source_type="file",
                target_type="symbol",
                evidence_file_id=exp.file_id,
                evidence_start_line=exp.start_line,
                evidence_end_line=exp.end_line,
                evidence=f"export {exp.name}",
                target_file=exp.file_path,
            ))

    return edges

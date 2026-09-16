"""Symbol investigation service.

Retrieves the local neighborhood (depth 1) around a parsed symbol: definition
location, callers, callees, references, dependencies, related tests, exports,
relevant import context, unresolved/external edges, related files derived only
from real graph relationships, and source context for the frontend highlight.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.analysis.investigation.models import InvestigationEdge, InvestigationResult
from app.analysis.search import limits
from app.models.orm import FileRecord, Relationship, Symbol

SourceSymbol = aliased(Symbol, name="src_symbol")
TargetSymbol = aliased(Symbol, name="tgt_symbol")
TargetFile = aliased(FileRecord, name="tgt_file")


class SymbolNotFoundError(LookupError):
    pass


def _edge_from_row(rel, evidence_path, source_name, target_name, target_file_path) -> InvestigationEdge:
    return InvestigationEdge(
        relationship_id=rel.id,
        relation=rel.type,
        source_symbol_id=rel.source_symbol_id,
        source_name=source_name,
        source_file=evidence_path,
        source_line=rel.evidence_start_line,
        target_symbol_id=rel.target_symbol_id,
        target_name=target_name,
        target_type=rel.target_type,
        target_file=target_file_path or rel.target_file,
        target_line=None,
        resolution_status=rel.resolution_status,
        evidence=rel.evidence,
    )


class InvestigationService:
    """Builds a depth-1 investigation view for a single symbol."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def investigate(
        self, repository_id: int, symbol_id: int
    ) -> InvestigationResult:
        row = (
            (
                await self.db.execute(
                    select(Symbol, FileRecord.path)
                    .join(FileRecord, Symbol.file_id == FileRecord.id)
                    .where(
                        Symbol.repository_id == repository_id,
                        Symbol.id == symbol_id,
                    )
                )
            )
            .first()
        )
        if row is None:
            raise SymbolNotFoundError(f"symbol {symbol_id} not found")

        sym, path = row

        definition = {
            "id": sym.id,
            "name": sym.name,
            "qualified_name": sym.qualified_name,
            "kind": sym.kind,
            "language": sym.language,
            "file_path": path,
            "line_start": sym.line_start,
            "line_end": sym.line_end,
            "start_column": sym.start_column,
            "end_column": sym.end_column,
            "exported": sym.exported,
            "signature": sym.signature,
            "docstring": sym.docstring,
            "parent_symbol_id": sym.parent_symbol_id,
        }

        base = (
            select(
                Relationship,
                FileRecord.path,
                SourceSymbol.name,
                TargetSymbol.name,
                TargetFile.path,
            )
            .join(FileRecord, Relationship.evidence_file_id == FileRecord.id, isouter=True)
            .join(SourceSymbol, Relationship.source_symbol_id == SourceSymbol.id, isouter=True)
            .join(TargetSymbol, Relationship.target_symbol_id == TargetSymbol.id, isouter=True)
            .join(TargetFile, TargetSymbol.file_id == TargetFile.id, isouter=True)
            .where(
                Relationship.repository_id == repository_id,
                (Relationship.source_symbol_id == symbol_id)
                | (Relationship.target_symbol_id == symbol_id),
            )
            .order_by(Relationship.type, Relationship.id)
            .limit(limits.MAX_GROUP_EDGES * 12)
        )
        rows = (await self.db.execute(base)).all()
        edges = [
            _edge_from_row(rel, p, src, tgt, tgt_path)
            for rel, p, src, tgt, tgt_path in rows
        ]

        result = InvestigationResult(
            symbol=definition,
            definition=definition,
            source_context={
                "file": path,
                "line_start": sym.line_start,
                "line_end": sym.line_end,
            },
        )

        for edge in edges:
            if edge.resolution_status == "EXTERNAL":
                result.external.append(edge)
            elif edge.resolution_status == "UNRESOLVED":
                result.unresolved.append(edge)
            else:
                self._route(edge, result, symbol_id)

        related: set[str] = {path}
        for edge in edges:
            for fp in (edge.source_file, edge.target_file):
                if fp:
                    related.add(fp)
        result.related_files = sorted(related)[: limits.MAX_RELATED_FILES]

        return result

    def _route(self, edge: InvestigationEdge, result: InvestigationResult, symbol_id: int):
        relation = edge.relation
        if relation in ("DEFINES", "EXTENDS", "IMPLEMENTS"):
            result.dependencies.append(edge)
            return
        if relation == "CALLS":
            if edge.source_symbol_id == symbol_id:
                result.callees.append(edge)
            elif edge.target_symbol_id == symbol_id:
                result.callers.append(edge)
            return
        if relation == "REFERENCES":
            result.references.append(edge)
            return
        if relation == "TESTS":
            result.tests.append(edge)
            return
        if relation == "EXPORTS":
            result.exports.append(edge)
            return
        if relation == "IMPORTS":
            result.import_context.append(edge)
            return
        result.dependencies.append(edge)
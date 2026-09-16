"""Helpers to serialize ORM models into API schemas."""
from __future__ import annotations

import json

from app.models.orm import Export, Import, ParseResult, Relationship, Repository, Symbol
from app.models.schemas import (
    AnalysisStatus,
    Confidence,
    ExportInfo,
    ImportInfo,
    RelationshipInfo,
    RelationshipResolutionStatus,
    RelationshipType,
    RepositoryInfo,
    SymbolInfo,
)


def repository_to_info(repository: Repository) -> RepositoryInfo:
    """Convert a Repository ORM row into its API representation."""
    languages: dict[str, float] = {}
    if repository.languages:
        try:
            languages = json.loads(repository.languages)
        except (json.JSONDecodeError, TypeError):
            languages = {}

    return RepositoryInfo(
        id=repository.id,
        url=repository.url,
        owner=repository.owner,
        name=repository.name,
        branch=repository.branch,
        commit_sha=repository.commit_sha,
        source_size_bytes=repository.source_size_bytes,
        status=AnalysisStatus(repository.status),
        file_count=repository.file_count,
        symbol_count=repository.symbol_count,
        relationship_count=repository.relationship_count,
        analyzed=repository.analyzed,
        analyzed_at=repository.analyzed_at,
        parsed_file_count=repository.parsed_file_count,
        import_count=repository.import_count,
        export_count=repository.export_count,
        languages=languages,
        error_message=repository.error_message,
        created_at=repository.created_at,
    )


def symbol_to_info(symbol: Symbol, file_path: str) -> SymbolInfo:
    """Convert a Symbol ORM row into its API representation."""
    return SymbolInfo(
        id=symbol.id,
        name=symbol.name,
        qualified_name=symbol.qualified_name,
        kind=symbol.kind,
        language=symbol.language,
        file_path=file_path,
        line_start=symbol.line_start,
        line_end=symbol.line_end,
        start_column=symbol.start_column,
        end_column=symbol.end_column,
        exported=symbol.exported,
        signature=symbol.signature,
        docstring=symbol.docstring,
        parent_symbol_id=symbol.parent_symbol_id,
    )


def import_to_info(import_row: Import, file_path: str) -> ImportInfo:
    return ImportInfo(
        id=import_row.id,
        file_path=file_path,
        source=import_row.source,
        imported_name=import_row.imported_name,
        alias=import_row.alias,
        kind=import_row.kind,
        start_line=import_row.start_line,
        end_line=import_row.end_line,
    )


def export_to_info(export_row: Export, file_path: str) -> ExportInfo:
    return ExportInfo(
        id=export_row.id,
        file_path=file_path,
        name=export_row.name,
        kind=export_row.kind,
        start_line=export_row.start_line,
        end_line=export_row.end_line,
    )


def relationship_to_info(
    rel: Relationship,
    evidence_path: str | None,
    source_name: str | None = None,
    target_name: str | None = None,
) -> RelationshipInfo:
    """Convert a Relationship ORM row into its API representation."""
    return RelationshipInfo(
        id=rel.id,
        source_symbol_id=rel.source_symbol_id,
        target_symbol_id=rel.target_symbol_id,
        type=RelationshipType(rel.type),
        resolution_status=RelationshipResolutionStatus(rel.resolution_status),
        source_type=rel.source_type,
        target_type=rel.target_type,
        source_file=evidence_path,
        source_line=rel.evidence_start_line,
        target_file=rel.target_file,
        evidence=rel.evidence,
        source_symbol_name=source_name,
        target_symbol_name=target_name,
    )
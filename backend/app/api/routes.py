"""API routes for RepoLens.

Phase 1 exposes real repository ingestion: validate -> clone -> discover ->
store metadata. Errors are mapped to clear, user-friendly messages; technical
details are logged server-side.

Phase 2 adds the deterministic parse pipeline: per-repository parse runs and
read endpoints for symbols, imports, exports, and file content.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.analysis.service import (
    AnalysisService,
    RepositoryBusyError,
    RepositoryNotFoundError,
    RepositoryNotReadyError,
)
from app.analysis.relationships.engine import RelationshipEngine
from app.analysis.diff.engine import DiffEngine
from app.analysis.diff.git_service import (
    GitError,
    RevisionNotFoundError,
    RevisionValidationError,
)
from app.analysis.diff.service import (
    DiffNotFoundError,
    diff_file_to_info,
    diff_to_detail,
    diff_to_info,
    get_changed_lines,
    get_diff,
    get_diff_files,
    get_diff_hunks,
    hunk_to_info,
)
from app.analysis.diff import limits as diff_limits
from app.analysis.impact import (
    ImpactNotFoundError,
    analysis_to_info,
    get_impact_analysis,
    run_impact_analysis,
)
from app.analysis.impact.limits import (
    MAX_IMPACT_DEPTH_CEILING,
    MAX_IMPACT_DEPTH_DEFAULT,
)
from app.analysis.review import (
    ReviewNotFoundError,
    create_review,
    entry_to_info,
    get_review,
    item_to_info,
    review_to_info,
    update_entry,
    update_item,
)
from app.core.database import get_db
from app.models.orm import (
    Diff,
    DiffHunk,
    Export,
    FileRecord,
    Import,
    ImpactAnalysis,
    Relationship,
    Repository,
    Review,
    Symbol,
)
from app.analysis.search import limits
from app.analysis.search.service import run_search, validate_query, QueryError
from app.analysis.investigation.service import InvestigationService, SymbolNotFoundError
from app.ai import (
    LensProviderFailure,
    LensService,
    LensUnavailableError,
    build_change_context,
    build_impact_context,
    build_review_context,
    build_unresolved_context,
)
from app.ai.context import DiffFileNotFoundError as AiDiffFileNotFoundError
from app.models.schemas import (
    AnalysisSummary,
    DiffChangedLineInfo,
    DiffDetail,
    DiffFileInfo,
    DiffHunkList,
    DiffHunkInfo,
    DiffInfo,
    DiffInput,
    DiffSymbolInfo,
    ExportInfo,
    FileContent,
    FileSearchHit,
    ImpactAnalysisInfo,
    ImpactInput,
    ImportInfo,
    InvestigationGroup,
    InvestigationResponse,
    LensChangeRequest,
    LensImpactRequest,
    LensResponse,
    LensReviewRequest,
    LensUnresolvedRequest,
    RecentActivityInfo,
    RelatedFileInfo,
    RelationshipInfo,
    RelationshipSummary,
    RelationshipsResponse,
    RepositoryInfo,
    RepositoryInput,
    ReviewCreateInput,
    ReviewEntryInfo,
    ReviewEntryUpdateInput,
    ReviewInfo,
    ReviewItemInfo,
    ReviewItemUpdateInput,
    SearchResponse,
    SourceContext,
    SymbolInfo,
    SymbolSearchHit,
    SymbolsResponse,
    TextSearchHit,
)
from app.models.serializers import (
    export_to_info,
    import_to_info,
    relationship_to_info,
    repository_to_info,
    symbol_to_info,
)
from app.repositories.discovery import PathEscapeError
from app.repositories.errors import RepositoryIngestionError
from app.services.ingestion_service import IngestionService, remove_repository

logger = logging.getLogger("repolens.api")

router = APIRouter()

SourceSymbol = aliased(Symbol)
TargetSymbol = aliased(Symbol)


@router.get("/health")
async def health() -> dict:
    """Liveness check used by load balancers and the frontend."""
    from app.config import get_settings

    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.version,
    }


@router.get("/")
async def root() -> dict:
    """Simple API overview for discovery."""
    from app.config import get_settings

    settings = get_settings()
    return {
        "service": settings.app_name,
        "version": settings.version,
        "docs": "/docs",
        "health": "/api/health",
    }


# --- Repository lifecycle ----------------------------------------------------


@router.post(
    "/repositories",
    response_model=RepositoryInfo,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_repository(
    payload: RepositoryInput, db: AsyncSession = Depends(get_db)
) -> RepositoryInfo:
    """Validate a GitHub URL, clone it safely, and index its files."""
    service = IngestionService(db)
    try:
        repository = await service.ingest_from_url(payload.url)
    except RepositoryIngestionError as exc:
        logger.warning("ingestion error [%s]: %s", exc.code, exc.message)
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.message,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("unhandled ingestion exception")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Repository ingestion failed unexpectedly. Please try again.",
        ) from exc
    return repository_to_info(repository)


@router.get("/repositories", response_model=list[RepositoryInfo])
async def list_repositories(db: AsyncSession = Depends(get_db)) -> list[RepositoryInfo]:
    """List repositories stored locally (newest first)."""
    rows = (
        await db.execute(
            select(Repository).order_by(Repository.id.desc()).limit(200)
        )
    ).scalars().all()
    return [repository_to_info(r) for r in rows]


@router.get("/repositories/{repository_id}", response_model=RepositoryInfo)
async def get_repository(
    repository_id: int, db: AsyncSession = Depends(get_db)
) -> RepositoryInfo:
    """Return metadata for one repository."""
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )
    return repository_to_info(row)


@router.post(
    "/repositories/{repository_id}/parse",
    response_model=AnalysisSummary,
)
async def parse_repository(
    repository_id: int, db: AsyncSession = Depends(get_db)
) -> AnalysisSummary:
    """Run the deterministic parse pipeline for a ready repository.

    Parses every stored source file with tree-sitter, persisting symbols,
    imports, exports, and per-file parse results. Returns real counters.
    """
    analysis = AnalysisService(db)
    try:
        summary = await analysis.parse_repository(repository_id)
    except RepositoryNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )
    except RepositoryNotReadyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository is not ready to analyze yet.",
        )
    except RepositoryBusyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository is already being analyzed.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("unhandled analysis exception")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Analysis failed unexpectedly. Please try again.",
        ) from exc
    return summary


@router.get(
    "/repositories/{repository_id}/analysis",
    response_model=AnalysisSummary,
)
async def get_analysis_summary(
    repository_id: int, db: AsyncSession = Depends(get_db)
) -> AnalysisSummary:
    """Return the latest analysis summary for a repository (no run triggered)."""
    analysis = AnalysisService(db)
    try:
        summary = await analysis.get_summary(repository_id)
    except RepositoryNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )
    return summary


# --- Analysis read endpoints -------------------------------------------------


@router.get("/repositories/{repository_id}/symbols", response_model=SymbolsResponse)
async def list_symbols(
    repository_id: int,
    file: str | None = Query(None, description="Filter by file path substring"),
    name: str | None = Query(None, description="Filter by symbol name substring"),
    kind: str | None = Query(None, description="Filter by symbol kind"),
    language: str | None = Query(None, description="Filter by parse language"),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> SymbolsResponse:
    """List extracted symbols with optional filters."""
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )

    conditions = [Symbol.repository_id == repository_id]
    if file:
        conditions.append(FileRecord.path.ilike(f"%{file}%"))
    if name:
        conditions.append(Symbol.name.ilike(f"%{name}%"))
    if kind:
        conditions.append(Symbol.kind.ilike(kind))
    if language:
        conditions.append(Symbol.language == language)

    base = select(Symbol, FileRecord.path).join(
        FileRecord, Symbol.file_id == FileRecord.id
    ).where(*conditions)

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    rows = (
        await db.execute(
            base.order_by(
                FileRecord.path, Symbol.line_start, Symbol.start_column, Symbol.id
            ).offset(offset).limit(limit)
        )
    ).all()

    items = [symbol_to_info(symbol, path) for symbol, path in rows]
    return SymbolsResponse(items=items, total=total)


@router.get("/repositories/{repository_id}/imports", response_model=list[ImportInfo])
async def list_imports(
    repository_id: int,
    file: str | None = Query(None, description="Filter by file path substring"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[ImportInfo]:
    """List extracted imports."""
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )

    base = select(Import, FileRecord.path).join(
        FileRecord, Import.file_id == FileRecord.id
    ).where(Import.repository_id == repository_id)
    if file:
        base = base.where(FileRecord.path.ilike(f"%{file}%"))

    rows = (
        await db.execute(
            base.order_by(FileRecord.path, Import.start_line, Import.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [import_to_info(import_row, path) for import_row, path in rows]


@router.get("/repositories/{repository_id}/exports", response_model=list[ExportInfo])
async def list_exports(
    repository_id: int,
    file: str | None = Query(None, description="Filter by file path substring"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[ExportInfo]:
    """List extracted JavaScript/TypeScript exports."""
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )

    base = select(Export, FileRecord.path).join(
        FileRecord, Export.file_id == FileRecord.id
    ).where(Export.repository_id == repository_id)
    if file:
        base = base.where(FileRecord.path.ilike(f"%{file}%"))

    rows = (
        await db.execute(
            base.order_by(FileRecord.path, Export.start_line, Export.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [export_to_info(export_row, path) for export_row, path in rows]


@router.get("/repositories/{repository_id}/content", response_model=FileContent)
async def get_file_content(
    repository_id: int,
    path: str = Query(
        ...,
        description="Repository-relative file path",
        min_length=1,
        max_length=2000,
    ),
    db: AsyncSession = Depends(get_db),
) -> FileContent:
    """Return one repository file's contents, blocked from path escapes."""
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )

    try:
        content = await AnalysisService(db).read_source(row, path)
    except (PathEscapeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except OverflowError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        ) from exc

    decoded = content.decode("utf-8", errors="replace")
    file_row = (
        await db.execute(
            select(FileRecord).where(
                FileRecord.repository_id == repository_id,
                FileRecord.path == path,
            )
        )
    ).scalar_one_or_none()
    return FileContent(
        path=path,
        content=decoded,
        language=file_row.language if file_row else None,
        line_count=len(decoded.splitlines()),
    )


@router.delete(
    "/repositories/{repository_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_repository(
    repository_id: int, db: AsyncSession = Depends(get_db)
) -> None:
    """Delete a repository and clean up its cloned workspace."""
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )
    await remove_repository(db, row)
    logger.info("deleted repository id=%d", repository_id)


# --- Relationships (Phase 3) -------------------------------------------------


@router.post(
    "/repositories/{repository_id}/relationships",
    response_model=RelationshipSummary,
)
async def build_relationships(
    repository_id: int, db: AsyncSession = Depends(get_db)
) -> RelationshipSummary:
    """Run the relationship engine and persist resulting graph edges."""
    engine = RelationshipEngine(db)
    try:
        result = await engine.build_relationships(repository_id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("unhandled relationship build exception")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Relationship analysis failed unexpectedly. Please try again.",
        ) from exc
    return RelationshipSummary(**result)


# --- Search (Phase 4) --------------------------------------------------------


@router.get(
    "/repositories/{repository_id}/search",
    response_model=SearchResponse,
)
async def search_repository(
    repository_id: int,
    q: str = Query(..., min_length=1, description="Search query"),
    type: str = Query("symbol", description="Search type: symbol, file, text"),
    case_sensitive: bool = Query(False, description="Case-sensitive text search"),
    limit: int = Query(50, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Result offset"),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """Search symbols, files, or source text within a repository."""
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )
    try:
        clean_query = validate_query(q)
    except QueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if type not in ("symbol", "file", "text"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid search type: {type}. Must be symbol, file, or text.",
        )

    try:
        results = await run_search(
            db, row, clean_query, type, case_sensitive, limit, offset
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("search failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search failed unexpectedly.",
        ) from exc

    return SearchResponse(
        query=clean_query,
        type=type,
        total=len(results),
        results=results,
    )


# --- Investigation (Phase 4) -------------------------------------------------


@router.get(
    "/repositories/{repository_id}/symbols/{symbol_id}/investigation",
    response_model=InvestigationResponse,
)
async def investigate_symbol(
    repository_id: int,
    symbol_id: int,
    db: AsyncSession = Depends(get_db),
) -> InvestigationResponse:
    """Return depth-1 investigation for a symbol: definition, callers, callees, references, tests, imports, exports, related files."""
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )

    service = InvestigationService(db)
    try:
        result = await service.investigate(repository_id, symbol_id)
    except SymbolNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol {symbol_id} not found in repository {repository_id}.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("investigation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Investigation failed unexpectedly.",
        ) from exc

    sym = result.symbol
    sym_info = SymbolInfo(
        id=sym["id"],
        name=sym["name"],
        qualified_name=sym.get("qualified_name"),
        kind=sym["kind"],
        language=sym.get("language"),
        file_path=sym.get("file_path", ""),
        line_start=sym.get("line_start", 0),
        line_end=sym.get("line_end", 0),
        start_column=sym.get("start_column", 0),
        end_column=sym.get("end_column", 0),
        exported=sym.get("exported", False),
        signature=sym.get("signature"),
        docstring=sym.get("docstring"),
        parent_symbol_id=sym.get("parent_symbol_id"),
    )

    sc = result.source_context
    source_ctx = SourceContext(
        path=sc.get("file"),
        start_line=sc.get("line_start", 0),
        end_line=sc.get("line_end", 0),
    )

    def _edge_to_rel(edge):
        return RelationshipInfo(
            id=edge.relationship_id,
            source_symbol_id=edge.source_symbol_id,
            target_symbol_id=edge.target_symbol_id,
            type=edge.relation,
            resolution_status=edge.resolution_status,
            source_type="symbol",
            target_type=edge.target_type,
            source_file=edge.source_file,
            source_line=edge.source_line,
            target_file=edge.target_file,
            target_line=edge.target_line,
            evidence=edge.evidence,
            source_symbol_name=edge.source_name,
            target_symbol_name=edge.target_name,
        )

    def _category_meta(category: str):
        return {
            "callers": ("callers", "Callers", "incoming"),
            "callees": ("callees", "Callees", "outgoing"),
            "references": ("references", "References", "incoming"),
            "dependencies": ("dependencies", "Dependencies", "outgoing"),
            "tests": ("tests", "Tests", "incoming"),
            "exports": ("exports", "Exports", "outgoing"),
            "import_context": ("imports", "Imports", "context"),
        }.get(category, (category, category, "context"))

    groups = []
    for cat in ("callers", "callees", "references", "dependencies", "tests", "exports", "import_context"):
        edges = getattr(result, cat, [])
        if edges:
            key, label, direction = _category_meta(cat)
            groups.append(
                InvestigationGroup(
                    category=key,
                    label=label,
                    direction=direction,
                    count=len(edges),
                    edges=[_edge_to_rel(e) for e in edges],
                )
            )

    related_file_rows = []
    for fp in result.related_files:
        file_row = (
            await db.execute(
                select(FileRecord).where(
                    FileRecord.repository_id == repository_id,
                    FileRecord.path == fp,
                )
            )
        ).scalar_one_or_none()
        if file_row:
            sym_count = (
                await db.execute(
                    select(func.count())
                    .select_from(Symbol)
                    .where(Symbol.file_id == file_row.id)
                )
            ).scalar_one()
            rel_count = (
                await db.execute(
                    select(func.count())
                    .select_from(Relationship)
                    .where(
                        Relationship.repository_id == repository_id,
                        Relationship.evidence_file_id == file_row.id,
                    )
                )
            ).scalar_one()
            related_file_rows.append(
                RelatedFileInfo(
                    file_id=file_row.id,
                    path=file_row.path,
                    language=file_row.language,
                    line_count=file_row.line_count or 0,
                    size_bytes=file_row.size_bytes or 0,
                    symbol_count=sym_count or 0,
                    relationship_count=rel_count or 0,
                )
            )

    unresolved = [_edge_to_rel(e) for e in result.unresolved]
    external = [_edge_to_rel(e) for e in result.external]

    return InvestigationResponse(
        repository_id=repository_id,
        symbol=sym_info,
        file=RelatedFileInfo(
            file_id=0,
            path=sc.get("file", ""),
        ),
        groups=groups,
        unresolved=unresolved,
        external=external,
        related_files=related_file_rows,
        source_context=source_ctx,
    )


@router.get(
    "/repositories/{repository_id}/relationships",
    response_model=RelationshipsResponse,
)
async def list_relationships(
    repository_id: int,
    type: str | None = Query(None, description="Filter by relationship type"),
    status_filter: str | None = Query(None, alias="status", description="Filter by resolution status"),
    file: str | None = Query(None, description="Filter by file path substring"),
    source: str | None = Query(None, description="Filter by source symbol name"),
    target: str | None = Query(None, description="Filter by target symbol name"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> RelationshipsResponse:
    """List relationships with optional filters."""
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )

    conditions = [Relationship.repository_id == repository_id]
    if type:
        conditions.append(Relationship.type == type.upper())
    if status_filter:
        conditions.append(Relationship.resolution_status == status_filter.upper())
    if file:
        conditions.append(
            FileRecord.path.ilike(f"%{file}%")
        )
    if source:
        conditions.append(
            Relationship.source_symbol_id.in_(
                select(Symbol.id).where(Symbol.name.ilike(f"%{source}%"))
            )
        )
    if target:
        conditions.append(
            Relationship.target_symbol_id.in_(
                select(Symbol.id).where(Symbol.name.ilike(f"%{target}%"))
            )
        )

    base = (
        select(
            Relationship,
            FileRecord.path,
            SourceSymbol.name,
            TargetSymbol.name,
        )
        .join(FileRecord, Relationship.evidence_file_id == FileRecord.id, isouter=True)
        .join(SourceSymbol, Relationship.source_symbol_id == SourceSymbol.id, isouter=True)
        .join(TargetSymbol, Relationship.target_symbol_id == TargetSymbol.id, isouter=True)
        .where(*conditions)
    )

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    rows = (
        await db.execute(
            base.order_by(Relationship.id).offset(offset).limit(limit)
        )
    ).all()

    items = [
        relationship_to_info(rel, evidence_path, source_name, target_name)
        for rel, evidence_path, source_name, target_name in rows
    ]
    return RelationshipsResponse(items=items, total=total)


@router.get(
    "/repositories/{repository_id}/symbols/{symbol_id}/relationships",
    response_model=RelationshipsResponse,
)
async def get_symbol_neighborhood(
    repository_id: int,
    symbol_id: int,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> RelationshipsResponse:
    """Return incoming + outgoing relationships for a symbol."""
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )

    base = (
        select(
            Relationship,
            FileRecord.path,
            SourceSymbol.name,
            TargetSymbol.name,
        )
        .join(FileRecord, Relationship.evidence_file_id == FileRecord.id, isouter=True)
        .join(SourceSymbol, Relationship.source_symbol_id == SourceSymbol.id, isouter=True)
        .join(TargetSymbol, Relationship.target_symbol_id == TargetSymbol.id, isouter=True)
        .where(
            Relationship.repository_id == repository_id,
            (Relationship.source_symbol_id == symbol_id)
            | (Relationship.target_symbol_id == symbol_id),
        )
    )

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    rows = (
        await db.execute(
            base.order_by(Relationship.id).offset(offset).limit(limit)
        )
    ).all()

    items = [
        relationship_to_info(rel, evidence_path, source_name, target_name)
        for rel, evidence_path, source_name, target_name in rows
    ]
    return RelationshipsResponse(items=items, total=total)


# --- Git Diff (Phase 5) ----------------------------------------------------------

@router.post(
    "/repositories/{repository_id}/diff",
    response_model=DiffInfo,
    status_code=status.HTTP_201_CREATED,
)
async def create_diff(
    repository_id: int,
    payload: DiffInput,
    db: AsyncSession = Depends(get_db),
) -> DiffInfo:
    """Analyze the git diff between two revisions of a repository.

    Determines what changed (files, lines, symbols) — not impact.
    """
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )

    engine = DiffEngine(db)
    try:
        summary = await engine.analyze(
            repository_id,
            payload.base_revision,
            payload.head_revision,
        )
    except RevisionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RevisionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except GitError as exc:
        logger.warning("git error during diff analysis: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("unhandled diff analysis exception")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Diff analysis failed unexpectedly.",
        ) from exc

    diff = await db.get(Diff, summary["id"])
    return await diff_to_info(diff)


@router.get(
    "/repositories/{repository_id}/diff/{diff_id}",
    response_model=DiffDetail,
)
async def get_diff_detail_endpoint(
    repository_id: int,
    diff_id: int,
    db: AsyncSession = Depends(get_db),
) -> DiffDetail:
    """Return a persisted diff's detail: files + symbols."""
    try:
        diff = await get_diff(db, repository_id, diff_id)
    except DiffNotFoundError:
        raise HTTPException(status_code=404, detail="Diff not found.")
    return await diff_to_detail(db, diff)


@router.get(
    "/repositories/{repository_id}/diff/{diff_id}/files",
    response_model=list[DiffFileInfo],
)
async def get_diff_files_endpoint(
    repository_id: int,
    diff_id: int,
    db: AsyncSession = Depends(get_db),
) -> list[DiffFileInfo]:
    """Return the changed file list for a diff."""
    await _get_diff_or_404(db, repository_id, diff_id)
    files = await get_diff_files(db, diff_id)
    return [await diff_file_to_info(df) for df in files]


@router.get(
    "/repositories/{repository_id}/diff/{diff_id}/symbols",
    response_model=list[DiffSymbolInfo],
)
async def get_diff_symbols_endpoint(
    repository_id: int,
    diff_id: int,
    db: AsyncSession = Depends(get_db),
) -> list[DiffSymbolInfo]:
    """Return the changed symbols for a diff."""
    await _get_diff_or_404(db, repository_id, diff_id)
    from app.analysis.diff.service import get_diff_symbols

    symbols = await get_diff_symbols(db, diff_id)
    return [
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
        for s in symbols
    ]


@router.get(
    "/repositories/{repository_id}/diff/{diff_id}/files/{diff_file_id}",
    response_model=DiffHunkList,
)
async def get_diff_file_hunks_endpoint(
    repository_id: int,
    diff_id: int,
    diff_file_id: int,
    db: AsyncSession = Depends(get_db),
) -> DiffHunkList:
    """Return hunks + changed lines for one changed file."""
    await _get_diff_or_404(db, repository_id, diff_id)
    files = await get_diff_files(db, diff_id)
    target = None
    for f in files:
        if f.id == diff_file_id:
            target = f
            break
    if target is None:
        raise HTTPException(status_code=404, detail="Diff file not found.")

    hunks = await get_diff_hunks(db, diff_file_id)
    hunk_infos = []
    for hunk in hunks:
        lines = await get_changed_lines(db, hunk.id)
        hunk_infos.append(
            DiffHunkInfo(
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
                    )
                    for l in lines
                ],
            )
        )

    return DiffHunkList(file=await diff_file_to_info(target), hunks=hunk_infos)


async def _get_diff_or_404(
    db: AsyncSession, repository_id: int, diff_id: int
) -> None:
    """Raise 404 if the diff does not exist for this repository."""
    try:
        await get_diff(db, repository_id, diff_id)
    except DiffNotFoundError:
        raise HTTPException(status_code=404, detail="Diff not found.")


# --- Phase 6: Change impact simulation ----------------------------------------


@router.post(
    "/repositories/{repository_id}/diff/{diff_id}/impact",
    response_model=ImpactAnalysisInfo,
    status_code=status.HTTP_201_CREATED,
)
async def create_impact_analysis(
    repository_id: int,
    diff_id: int,
    payload: ImpactInput | None = None,
    db: AsyncSession = Depends(get_db),
) -> ImpactAnalysisInfo:
    """Simulate impact of a persisted diff (idempotent per max_depth).

    Deterministic static analysis over observed repository relationships only.
    It identifies potentially affected code; it does not guarantee runtime
    impact or correctness.
    """
    if payload is None:
        payload = ImpactInput()
    if payload.max_depth > MAX_IMPACT_DEPTH_CEILING:
        raise HTTPException(
            status_code=422,
            detail=f"max_depth cannot exceed {MAX_IMPACT_DEPTH_CEILING}.",
        )
    try:
        return await run_impact_analysis(db, repository_id, diff_id, payload.max_depth)
    except DiffNotFoundError:
        raise HTTPException(status_code=404, detail="Diff not found.")


@router.get(
    "/repositories/{repository_id}/impact/{analysis_id}",
    response_model=ImpactAnalysisInfo,
)
async def get_impact_analysis_endpoint(
    repository_id: int,
    analysis_id: int,
    db: AsyncSession = Depends(get_db),
) -> ImpactAnalysisInfo:
    """Return a persisted impact analysis for a repository."""
    try:
        analysis = await get_impact_analysis(db, repository_id, analysis_id)
    except ImpactNotFoundError:
        raise HTTPException(status_code=404, detail="Impact analysis not found.")
    return await analysis_to_info(db, analysis)


# --- Phase 7: Deterministic change review workflow -----------------------------


@router.post(
    "/repositories/{repository_id}/impact/{analysis_id}/review",
    response_model=ReviewInfo,
)
async def create_review_endpoint(
    repository_id: int,
    analysis_id: int,
    payload: ReviewCreateInput | None = None,
    response: Response = None,  # type: ignore[assignment]
    db: AsyncSession = Depends(get_db),
) -> ReviewInfo:
    """Create (or reuse) a deterministic review for an impact analysis.

    Determining a review is idempotent: re-posting returns the existing review
    (200). Set ``{"regenerate": true}`` to force a fresh review (201) while
    carrying reviewer state forward by stable identity.
    """
    if payload is None:
        payload = ReviewCreateInput()
    try:
        review, created = await create_review(
            db, repository_id, analysis_id, payload.regenerate
        )
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail="Impact analysis not found.")
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return await review_to_info(db, review)


@router.get(
    "/repositories/{repository_id}/reviews/{review_id}",
    response_model=ReviewInfo,
)
async def get_review_endpoint(
    repository_id: int,
    review_id: int,
    db: AsyncSession = Depends(get_db),
) -> ReviewInfo:
    """Return a persisted review (repository-scoped)."""
    try:
        review = await get_review(db, repository_id, review_id)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail="Review not found.")
    return await review_to_info(db, review)


@router.patch(
    "/repositories/{repository_id}/reviews/{review_id}/items/{item_id}",
    response_model=ReviewItemInfo,
)
async def update_review_item_endpoint(
    repository_id: int,
    review_id: int,
    item_id: int,
    payload: ReviewItemUpdateInput,
    db: AsyncSession = Depends(get_db),
) -> ReviewItemInfo:
    """Update a review item's status and/or reviewer notes."""
    from app.analysis.review.service import ReviewItemNotFoundError, get_item

    try:
        await update_item(
            db,
            repository_id,
            review_id,
            item_id,
            status=payload.status,
            notes=payload.notes,
        )
        item = await get_item(db, repository_id, review_id, item_id)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail="Review not found.")
    except ReviewItemNotFoundError:
        raise HTTPException(status_code=404, detail="Review item not found.")
    return await item_to_info(db, item)


@router.patch(
    "/repositories/{repository_id}/reviews/{review_id}/items/{item_id}/entries/{entry_id}",
    response_model=ReviewEntryInfo,
)
async def update_review_entry_endpoint(
    repository_id: int,
    review_id: int,
    item_id: int,
    entry_id: int,
    payload: ReviewEntryUpdateInput,
    db: AsyncSession = Depends(get_db),
) -> ReviewEntryInfo:
    """Toggle a review entry (e.g. a test sub-checklist row)."""
    from app.analysis.review.service import ReviewItemNotFoundError, get_entry

    try:
        await update_entry(
            db,
            repository_id,
            review_id,
            item_id,
            entry_id,
            status=payload.status,
        )
        entry = await get_entry(db, repository_id, review_id, item_id, entry_id)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail="Review not found.")
    except ReviewItemNotFoundError:
        raise HTTPException(status_code=404, detail="Review entry not found.")
    return entry_to_info(entry)


# --- Phase 8: Overview recent activity ----------------------------------------


@router.get(
    "/repositories/{repository_id}/recent",
    response_model=RecentActivityInfo,
)
async def recent_activity_endpoint(
    repository_id: int, db: AsyncSession = Depends(get_db)
) -> RecentActivityInfo:
    """Return the most recent diff, impact analysis, and review for a repo.

    Real, persisted activity only — every row maps to something a user can open
    from the Overview page.
    """
    row = await db.get(Repository, repository_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Repository not found.")

    diffs = (
        (
            await db.execute(
                select(Diff)
                .where(Diff.repository_id == repository_id)
                .order_by(Diff.id.desc())
                .limit(3)
            )
        )
        .scalars()
        .all()
    )
    analyses = (
        (
            await db.execute(
                select(ImpactAnalysis)
                .where(ImpactAnalysis.repository_id == repository_id)
                .order_by(ImpactAnalysis.id.desc())
                .limit(3)
            )
        )
        .scalars()
        .all()
    )
    reviews = (
        (
            await db.execute(
                select(Review)
                .where(Review.repository_id == repository_id)
                .order_by(Review.id.desc())
                .limit(3)
            )
        )
        .scalars()
        .all()
    )

    return RecentActivityInfo(
        diffs=[await diff_to_info(d) for d in diffs],
        impact_analyses=[await analysis_to_info(db, a) for a in analyses],
        reviews=[await review_to_info(db, r) for r in reviews],
    )


# --- Phase 8: Lens AI explanation layer ---------------------------------------


async def _lens_explain(
    service: LensService, repository: Repository, context
) -> LensResponse:
    """Run Lens and translate its failure modes into HTTP errors.

    Predictive failures are user-facing and constant; the audit tray records
    the machine-readable reason either way.
    """
    try:
        return await service.explain(repository=repository, context=context)
    except LensUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lens is unavailable because no LLM provider is configured.",
        )
    except LensProviderFailure as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Lens could not generate an explanation ({exc.kind}).",
        )


@router.post(
    "/repositories/{repository_id}/lens/change",
    response_model=LensResponse,
)
async def lens_change_endpoint(
    repository_id: int,
    payload: LensChangeRequest,
    db: AsyncSession = Depends(get_db),
) -> LensResponse:
    """Lens: explain a persisted diff (optionally scoped to one file)."""
    repository = await db.get(Repository, repository_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found.")
    try:
        diff = await get_diff(db, repository_id, payload.diff_id)
    except DiffNotFoundError:
        raise HTTPException(status_code=404, detail="Diff not found.")
    service = LensService(db)
    try:
        context = await build_change_context(
            db, repository, diff, payload.diff_file_id, service.settings
        )
    except AiDiffFileNotFoundError:
        raise HTTPException(status_code=404, detail="Diff file not found.")
    return await _lens_explain(service, repository, context)


@router.post(
    "/repositories/{repository_id}/lens/impact",
    response_model=LensResponse,
)
async def lens_impact_endpoint(
    repository_id: int,
    payload: LensImpactRequest,
    db: AsyncSession = Depends(get_db),
) -> LensResponse:
    """Lens: explain an impact simulation for a repository."""
    repository = await db.get(Repository, repository_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found.")
    try:
        await get_impact_analysis(db, repository_id, payload.analysis_id)
    except ImpactNotFoundError:
        raise HTTPException(status_code=404, detail="Impact analysis not found.")
    service = LensService(db)
    context = await build_impact_context(
        db, repository, payload.analysis_id, service.settings
    )
    return await _lens_explain(service, repository, context)


@router.post(
    "/repositories/{repository_id}/lens/review",
    response_model=LensResponse,
)
async def lens_review_endpoint(
    repository_id: int,
    payload: LensReviewRequest,
    db: AsyncSession = Depends(get_db),
) -> LensResponse:
    """Lens: explain why a change review calls for the checks it does."""
    repository = await db.get(Repository, repository_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found.")
    try:
        await get_review(db, repository_id, payload.review_id)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail="Review not found.")
    service = LensService(db)
    context = await build_review_context(
        db, repository, payload.review_id, service.settings
    )
    return await _lens_explain(service, repository, context)


@router.post(
    "/repositories/{repository_id}/lens/unresolved",
    response_model=LensResponse,
)
async def lens_unresolved_endpoint(
    repository_id: int,
    payload: LensUnresolvedRequest,
    db: AsyncSession = Depends(get_db),
) -> LensResponse:
    """Lens: explain why impact resolution failed for specific call targets."""
    repository = await db.get(Repository, repository_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found.")
    try:
        await get_impact_analysis(db, repository_id, payload.analysis_id)
    except ImpactNotFoundError:
        raise HTTPException(status_code=404, detail="Impact analysis not found.")
    service = LensService(db)
    context = await build_unresolved_context(
        db, repository, payload.analysis_id, service.settings
    )
    return await _lens_explain(service, repository, context)
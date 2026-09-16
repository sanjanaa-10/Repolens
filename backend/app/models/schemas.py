"""Pydantic API schemas for RepoLens.

These define the wire contracts between the FastAPI backend and the React
frontend. They map closely to the normalized internal model.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AnalysisStatus(str, Enum):
    PENDING = "pending"
    CLONING = "cloning"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class Confidence(str, Enum):
    DIRECT = "DIRECT"
    INFERRED = "INFERRED"
    UNRESOLVED = "UNRESOLVED"
    EXTERNAL = "EXTERNAL"


class RelationshipType(str, Enum):
    IMPORTS = "IMPORTS"
    EXPORTS = "EXPORTS"
    DEFINES = "DEFINES"
    CALLS = "CALLS"
    REFERENCES = "REFERENCES"
    EXTENDS = "EXTENDS"
    IMPLEMENTS = "IMPLEMENTS"
    TESTS = "TESTS"
    DEPENDS_ON = "DEPENDS_ON"


class RelationshipResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    EXTERNAL = "EXTERNAL"


# --- Indexing ---------------------------------------------------------------


class RepositoryInput(BaseModel):
    url: str = Field(..., description="Public GitHub repository URL")


class RepositoryInfo(BaseModel):
    id: int
    url: str
    owner: str
    name: str
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    source_size_bytes: int = 0
    status: AnalysisStatus
    file_count: int = 0
    symbol_count: int = 0
    relationship_count: int = 0
    analyzed: bool = False
    analyzed_at: Optional[datetime] = None
    parsed_file_count: int = 0
    import_count: int = 0
    export_count: int = 0
    languages: dict[str, float] = Field(default_factory=dict)
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None


class FileNode(BaseModel):
    path: str
    name: str
    is_dir: bool
    language: Optional[str] = None
    size: int = 0


class FileContent(BaseModel):
    path: str
    content: str
    language: Optional[str] = None
    line_count: int = 0


class AnalysisProgress(BaseModel):
    step: str
    label: str
    progress_percent: int


# --- Investigation -----------------------------------------------------------


class SymbolInfo(BaseModel):
    id: int
    name: str
    qualified_name: Optional[str] = None
    kind: str
    language: Optional[str] = None
    file_path: str
    line_start: int
    line_end: int
    start_column: int = 0
    end_column: int = 0
    exported: bool = False
    signature: Optional[str] = None
    docstring: Optional[str] = None
    parent_symbol_id: Optional[int] = None


class ImportInfo(BaseModel):
    id: int
    file_path: str
    source: str
    imported_name: Optional[str] = None
    alias: Optional[str] = None
    kind: str
    start_line: int
    end_line: int


class ExportInfo(BaseModel):
    id: int
    file_path: str
    name: str
    kind: str
    start_line: int
    end_line: int


class ParseStatusBreakdown(BaseModel):
    parsed: int = 0
    syntax_error: int = 0
    unsupported: int = 0
    failed: int = 0


class AnalysisSummary(BaseModel):
    repository_id: int
    analyzed: bool
    analyzed_at: Optional[datetime] = None
    file_count: int = 0
    files_processed: int = 0
    symbol_count: int = 0
    import_count: int = 0
    export_count: int = 0
    statuses: ParseStatusBreakdown = Field(default_factory=ParseStatusBreakdown)
    parser_versions: dict[str, str] = Field(default_factory=dict)
    duration_ms: int = 0


class SymbolsResponse(BaseModel):
    items: list[SymbolInfo] = Field(default_factory=list)
    total: int = 0


class RelationshipInfo(BaseModel):
    id: int
    source_symbol_id: Optional[int] = None
    target_symbol_id: Optional[int] = None
    type: RelationshipType
    resolution_status: RelationshipResolutionStatus
    source_type: str = "symbol"
    target_type: str = "symbol"
    source_file: Optional[str] = None
    source_line: int = 0
    target_file: Optional[str] = None
    target_line: Optional[int] = None
    evidence: Optional[str] = None
    source_symbol_name: Optional[str] = None
    target_symbol_name: Optional[str] = None


class RelationshipSummary(BaseModel):
    repository_id: int
    status: str = "ready"
    relationships_created: int = 0
    resolved: int = 0
    external: int = 0
    unresolved: int = 0
    duration_ms: int = 0


class RelationshipsResponse(BaseModel):
    items: list[RelationshipInfo] = Field(default_factory=list)
    total: int = 0


class SymbolSearchHit(BaseModel):
    """Symbol-mode search result."""

    symbol_id: int
    name: str
    qualified_name: Optional[str] = None
    kind: str
    language: Optional[str] = None
    file_path: str
    line_start: int
    line_end: int
    exported: bool = False
    signature: Optional[str] = None


class FileSearchHit(BaseModel):
    """File-mode search result."""

    file_id: int
    path: str
    language: Optional[str] = None
    size_bytes: int = 0
    line_count: int = 0
    symbol_count: int = 0


class TextSearchHit(BaseModel):
    """Text-mode search result (line-level snippet)."""

    file_id: int
    path: str
    line_number: int
    snippet: str
    symbol_count: Optional[int] = None


class SearchResponse(BaseModel):
    """Unified response envelope returned by ``GET /search``."""

    query: str
    type: str  # symbol | file | text
    total: int
    results: list[SymbolSearchHit | FileSearchHit | TextSearchHit] = Field(
        default_factory=list
    )


class RelatedFileInfo(BaseModel):
    file_id: int
    path: str
    language: Optional[str] = None
    line_count: int = 0
    size_bytes: int = 0
    symbol_count: int = 0
    relationship_count: int = 0
    roles: list[str] = Field(default_factory=list)


class SourceContext(BaseModel):
    file_id: Optional[int] = None
    path: Optional[str] = None
    language: Optional[str] = None
    start_line: int = 0
    end_line: int = 0
    line_count: int = 0
    snippet: Optional[str] = None


class InvestigationGroup(BaseModel):
    category: str
    label: str
    direction: str  # incoming | outgoing | context
    count: int
    edges: list[RelationshipInfo] = Field(default_factory=list)


class InvestigationResponse(BaseModel):
    repository_id: int
    symbol: SymbolInfo
    file: RelatedFileInfo
    groups: list[InvestigationGroup] = Field(default_factory=list)
    unresolved: list[RelationshipInfo] = Field(default_factory=list)
    external: list[RelationshipInfo] = Field(default_factory=list)
    imports_context: list[ImportInfo] = Field(default_factory=list)
    related_files: list[RelatedFileInfo] = Field(default_factory=list)
    source_context: SourceContext = Field(default_factory=SourceContext)


# --- Impact / Review ---------------------------------------------------------


class AffectedEntity(BaseModel):
    classification: Confidence
    file_path: str
    symbol_id: Optional[int] = None
    symbol_name: Optional[str] = None
    symbol_kind: Optional[str] = None
    line: Optional[int] = None
    relationship_type: Optional[str] = None
    reason: str = ""
    evidence_line: Optional[int] = None


class ImpactResult(BaseModel):
    changed_entity: str
    direct_impact: list[AffectedEntity] = Field(default_factory=list)
    potential_impact: list[AffectedEntity] = Field(default_factory=list)
    tests_to_review: list[AffectedEntity] = Field(default_factory=list)
    unresolved: list[AffectedEntity] = Field(default_factory=list)
    external: list[AffectedEntity] = Field(default_factory=list)


class ReviewItem(BaseModel):
    category: str
    description: str
    file_path: Optional[str] = None
    symbol_id: Optional[int] = None
    evidence_line: Optional[int] = None
    checked: bool = False
    note: str = ""


class ReviewChecklist(BaseModel):
    impact_id: str
    items: list[ReviewItem] = Field(default_factory=list)


# --- Git Diff (Phase 5) -------------------------------------------------------


class FileStatus(str, Enum):
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    RENAMED = "RENAMED"
    COPIED = "COPIED"
    TYPE_CHANGED = "TYPE_CHANGED"
    UNTRACKED = "UNTRACKED"


class FileCategory(str, Enum):
    SOURCE = "SOURCE"
    TEST = "TEST"
    CONFIG = "CONFIG"
    DOCUMENTATION = "DOCUMENTATION"
    UNKNOWN = "UNKNOWN"


class DiffInput(BaseModel):
    base_revision: str = Field(
        ...,
        description="Base commit SHA (or ref)",
        min_length=1,
        max_length=100,
    )
    head_revision: str = Field(
        ...,
        description="Head commit SHA (or ref)",
        min_length=1,
        max_length=100,
    )


class DiffChangedLineInfo(BaseModel):
    side: str  # OLD | NEW
    line_number: int
    change_type: str  # ADDED | DELETED
    text: Optional[str] = None  # actual line content in the repo (Phase 8)


class DiffHunkInfo(BaseModel):
    id: int
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffChangedLineInfo] = Field(default_factory=list)


class DiffSymbolInfo(BaseModel):
    id: int
    symbol_id: int
    file_path: str
    symbol_name: str
    symbol_kind: str
    change_type: str  # ADDED | MODIFIED | DELETED
    added_lines: int = 0
    deleted_lines: int = 0


class DiffFileInfo(BaseModel):
    id: int
    path: str
    status: FileStatus
    old_path: Optional[str] = None
    new_path: Optional[str] = None
    additions: int = 0
    deletions: int = 0
    binary: bool = False
    file_category: FileCategory
    symbols_changed: int = 0
    symbol_change_types: dict[str, int] = Field(default_factory=dict)


class DiffInfo(BaseModel):
    id: int
    repository_id: int
    base_revision: str
    head_revision: str
    files_changed: int
    insertions: int
    deletions: int
    symbols_changed: int
    computed_at: Optional[datetime] = None


class DiffDetail(DiffInfo):
    files: list[DiffFileInfo] = Field(default_factory=list)
    symbols: list[DiffSymbolInfo] = Field(default_factory=list)


class DiffHunkList(BaseModel):
    file: DiffFileInfo
    hunks: list[DiffHunkInfo] = Field(default_factory=list)


# --- Change Impact Simulation (Phase 6) ---------------------------------------


class ImpactClass(str, Enum):
    DIRECT = "DIRECT"
    POTENTIAL = "POTENTIAL"
    UNRESOLVED = "UNRESOLVED"
    EXTERNAL = "EXTERNAL"


class ImpactInput(BaseModel):
    max_depth: int = Field(default=2, ge=1, le=6, description="Traversal depth")


class ImpactNodeInfo(BaseModel):
    node_type: str  # SYMBOL | FILE | UNRESOLVED | EXTERNAL
    node_id: Optional[int] = None
    name: str
    kind: Optional[str] = None
    file_path: Optional[str] = None
    file_category: str = "UNKNOWN"
    impact_class: ImpactClass
    depth: int = 0
    line_start: int = 0
    line_end: int = 0
    via: Optional[str] = None  # CALLS | REFERENCES | EXTENDS | IMPLEMENTS | TESTS | IMPORTS
    via_source: Optional[str] = None
    evidence_file: Optional[str] = None
    evidence_line: int = 0
    is_test: bool = False
    is_file_level_change: bool = False
    change_type: Optional[str] = None


class ImpactStepInfo(BaseModel):
    source: str
    relationship: str
    target: str
    evidence: str  # "path:line"


class ImpactPathInfo(BaseModel):
    root: str
    target: str
    target_node_type: str
    target_node_id: Optional[int] = None
    depth: int = 0
    steps: list[ImpactStepInfo] = Field(default_factory=list)


class ImpactSummary(BaseModel):
    changed_symbols: int = 0
    changed_files: int = 0
    direct: int = 0
    potential: int = 0
    affected_tests: int = 0
    unresolved: int = 0
    external: int = 0
    file_level_changes: int = 0
    nodes_total: int = 0
    truncated: bool = False
    truncated_reason: Optional[str] = None


class ImpactAnalysisInfo(BaseModel):
    analysis_id: int
    repository_id: int
    diff_id: int
    base_revision: str
    head_revision: str
    max_depth: int
    summary: ImpactSummary
    changed: list[ImpactNodeInfo] = Field(default_factory=list)
    potentially_affected: list[ImpactNodeInfo] = Field(default_factory=list)
    tests: list[ImpactNodeInfo] = Field(default_factory=list)
    unresolved: list[ImpactNodeInfo] = Field(default_factory=list)
    external: list[ImpactNodeInfo] = Field(default_factory=list)
    paths: list[ImpactPathInfo] = Field(default_factory=list)


# --- Change Review Workflow (Phase 7) ------------------------------------------


class ReviewItemType(str, Enum):
    CHANGED_CODE = "CHANGED_CODE"
    AFFECTED_CALLER = "AFFECTED_CALLER"
    AFFECTED_DEPENDENCY = "AFFECTED_DEPENDENCY"
    AFFECTED_TEST = "AFFECTED_TEST"
    UNRESOLVED_IMPACT = "UNRESOLVED_IMPACT"
    EXTERNAL_DEPENDENCY = "EXTERNAL_DEPENDENCY"
    CONFIGURATION_CHANGE = "CONFIGURATION_CHANGE"
    DOCUMENTATION_CHANGE = "DOCUMENTATION_CHANGE"


class ReviewPriority(str, Enum):
    REQUIRED = "REQUIRED"
    RECOMMENDED = "RECOMMENDED"
    INFORMATIONAL = "INFORMATIONAL"


class ReviewItemStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    SKIPPED = "SKIPPED"


class ReviewEntryStatus(str, Enum):
    OPEN = "OPEN"
    DONE = "DONE"


class ReviewCreateInput(BaseModel):
    regenerate: bool = Field(default=False, description="Force a fresh review")


class ReviewItemUpdateInput(BaseModel):
    status: Optional[ReviewItemStatus] = None
    notes: Optional[str] = Field(
        default=None, max_length=4000, description="Reviewer notes (plain text)"
    )


class ReviewEntryUpdateInput(BaseModel):
    status: ReviewEntryStatus = ReviewEntryStatus.DONE


class ReviewEntryInfo(BaseModel):
    entry_id: int
    key: str
    title: str
    description: str = ""
    kind: str = ""
    status: ReviewEntryStatus
    symbol_id: Optional[int] = None
    file_path: Optional[str] = None
    evidence_file: Optional[str] = None
    evidence_line: int = 0
    path_steps: list[ImpactStepInfo] = Field(default_factory=list)


class ReviewItemInfo(BaseModel):
    item_id: int
    item_type: ReviewItemType
    title: str
    description: str
    status: ReviewItemStatus
    priority: ReviewPriority
    source_type: Optional[str] = None
    source_id: Optional[int] = None
    evidence_file: Optional[str] = None
    evidence_start_line: int = 0
    evidence_end_line: int = 0
    group_via: Optional[str] = None
    notes: str = ""
    entries: list[ReviewEntryInfo] = Field(default_factory=list)


class ReviewSummaryInfo(BaseModel):
    total_items: int = 0
    required: int = 0
    recommended: int = 0
    informational: int = 0
    completed: int = 0
    open: int = 0
    in_progress: int = 0
    skipped: int = 0


class ReviewInfo(BaseModel):
    review_id: int
    repository_id: int
    diff_id: int
    impact_analysis_id: int
    title: str
    summary: str
    status: ReviewItemStatus
    base_revision: str
    head_revision: str
    max_depth: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    review_summary: ReviewSummaryInfo
    items: list[ReviewItemInfo] = Field(default_factory=list)


# --- Lens AI explanation layer (Phase 8) --------------------------------------


class LensKind(str, Enum):
    CHANGE = "change"
    IMPACT = "impact"
    REVIEW = "review"
    UNRESOLVED = "unresolved"


class LensChangeRequest(BaseModel):
    diff_id: int
    diff_file_id: Optional[int] = None


class LensImpactRequest(BaseModel):
    analysis_id: int


class LensReviewRequest(BaseModel):
    review_id: int


class LensUnresolvedRequest(BaseModel):
    analysis_id: int


class LensEvidenceInfo(BaseModel):
    """One piece of server-verified evidence a Lens explanation may cite.

    Built entirely from deterministic RepoLens data. The LLM never supplies
    these objects; it only selects from them by index, so it cannot fabricate
    file paths, symbols, or line numbers.
    """

    index: int
    kind: str
    # changed-symbol | changed-file | potentially-affected | test | external |
    # unresolved-call | review-item | review-entry | warning
    impact: Optional[str] = None
    # DIRECT | POTENTIAL | EXTERNAL | UNRESOLVED | REVIEW | REQUIRED | RECOMMENDED
    label: str
    file: Optional[str] = None
    detail: Optional[str] = None
    line: Optional[int] = None
    node_id: Optional[int] = None
    symbol_id: Optional[int] = None
    item_id: Optional[int] = None
    depth: Optional[int] = None
    snippet: Optional[str] = None


class LensResponse(BaseModel):
    kind: LensKind
    provider: str
    model: str
    prompt_version: str
    summary: str
    evidence: list[LensEvidenceInfo] = Field(default_factory=list)
    uncertainty: Optional[str] = None
    suggested_checks: list[str] = Field(default_factory=list)


class RecentActivityInfo(BaseModel):
    """Overview "Recent analysis" strip (Phase 8)."""

    diffs: list[DiffInfo] = Field(default_factory=list)
    impact_analyses: list[ImpactAnalysisInfo] = Field(default_factory=list)
    reviews: list[ReviewInfo] = Field(default_factory=list)

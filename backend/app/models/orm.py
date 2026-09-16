"""SQLAlchemy ORM models for RepoLens.

Phase 0 defines the core entities that later phases will populate with real
analysis results. These represent the normalized internal model:
Repository, File, Symbol, and Relationship.

Phase 2 extends Symbol with exact source locations and adds Import, Export,
and ParseResult tables populated by the deterministic parser pipeline.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    owner: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    branch: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    commit_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | cloning | indexing | ready | failed

    file_count: Mapped[int] = mapped_column(Integer, default=0)
    symbol_count: Mapped[int] = mapped_column(Integer, default=0)
    relationship_count: Mapped[int] = mapped_column(Integer, default=0)

    # Phase 2 parse summary (latest run).
    analyzed: Mapped[bool] = mapped_column(Boolean, default=False)
    analyzed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    parsed_file_count: Mapped[int] = mapped_column(Integer, default=0)
    syntax_error_count: Mapped[int] = mapped_column(Integer, default=0)
    unsupported_count: Mapped[int] = mapped_column(Integer, default=0)
    parse_error_count: Mapped[int] = mapped_column(Integer, default=0)
    import_count: Mapped[int] = mapped_column(Integer, default=0)
    export_count: Mapped[int] = mapped_column(Integer, default=0)

    languages: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON blob
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    local_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    files: Mapped[list["FileRecord"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(1000), index=True)
    language: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    analyzed: Mapped[bool] = mapped_column(default=False)

    repository: Mapped["Repository"] = relationship(back_populates="files")


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(500), index=True)
    qualified_name: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    kind: Mapped[str] = mapped_column(String(50))
    # FUNCTION | ASYNC_FUNCTION | CLASS | METHOD | ARROW_FUNCTION | INTERFACE
    # | TYPE_ALIAS | ENUM | IMPORT | VARIABLE
    language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    line_start: Mapped[int] = mapped_column(Integer, default=0)
    line_end: Mapped[int] = mapped_column(Integer, default=0)
    start_column: Mapped[int] = mapped_column(Integer, default=0)
    end_column: Mapped[int] = mapped_column(Integer, default=0)
    exported: Mapped[bool] = mapped_column(Boolean, default=False)
    signature: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    docstring: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parent_symbol_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("symbols.id", ondelete="SET NULL"), nullable=True
    )


class Import(Base):
    __tablename__ = "imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(1000))
    imported_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    alias: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    # python_import | python_from_import | es_module | commonjs
    start_line: Mapped[int] = mapped_column(Integer, default=0)
    end_line: Mapped[int] = mapped_column(Integer, default=0)


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(20))
    # named | default | star
    start_line: Mapped[int] = mapped_column(Integer, default=0)
    end_line: Mapped[int] = mapped_column(Integer, default=0)


class ParseResult(Base):
    __tablename__ = "parse_results"
    __table_args__ = (
        UniqueConstraint("repository_id", "file_id", name="uq_parse_repo_file"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    parser_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    # parsed | syntax_error | unsupported | failed
    source_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    symbol_count: Mapped[int] = mapped_column(Integer, default=0)
    import_count: Mapped[int] = mapped_column(Integer, default=0)
    export_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "source_symbol_id",
            "target_symbol_id",
            "type",
            "evidence_file_id",
            "evidence_start_line",
            name="uq_relationship_edge",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    source_symbol_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True, index=True
    )
    target_symbol_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(30), index=True)
    # DEFINES | IMPORTS | EXPORTS | CALLS | REFERENCES | EXTENDS | IMPLEMENTS | TESTS
    resolution_status: Mapped[str] = mapped_column(String(20), default="RESOLVED", index=True)
    # RESOLVED | UNRESOLVED | EXTERNAL
    source_type: Mapped[str] = mapped_column(String(20), default="symbol")
    # symbol | file
    target_type: Mapped[str] = mapped_column(String(20), default="symbol")
    # symbol | file | import
    evidence_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    evidence_start_line: Mapped[int] = mapped_column(Integer, default=0)
    evidence_end_line: Mapped[int] = mapped_column(Integer, default=0)
    evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_file: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- Phase 5: Git diff analysis ------------------------------------------------


class Diff(Base):
    __tablename__ = "diffs"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "base_revision",
            "head_revision",
            name="uq_diff_revisions",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    base_revision: Mapped[str] = mapped_column(String(64), index=True)
    head_revision: Mapped[str] = mapped_column(String(64), index=True)
    merge_base: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    files_changed: Mapped[int] = mapped_column(Integer, default=0)
    insertions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    symbols_changed: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DiffFile(Base):
    __tablename__ = "diff_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    diff_id: Mapped[int] = mapped_column(
        ForeignKey("diffs.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20), index=True)
    # ADDED | MODIFIED | DELETED | RENAMED | COPIED | TYPE_CHANGED | UNTRACKED
    old_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    new_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    binary: Mapped[bool] = mapped_column(default=False)
    file_category: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    # SOURCE | TEST | CONFIG | DOCUMENTATION | UNKNOWN
    old_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    new_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True
    )


class DiffHunk(Base):
    __tablename__ = "diff_hunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    diff_file_id: Mapped[int] = mapped_column(
        ForeignKey("diff_files.id", ondelete="CASCADE"), index=True
    )
    header: Mapped[str] = mapped_column(Text, default="")
    old_start: Mapped[int] = mapped_column(Integer, default=0)
    old_count: Mapped[int] = mapped_column(Integer, default=0)
    new_start: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")


class DiffChangedLine(Base):
    __tablename__ = "diff_changed_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    diff_hunk_id: Mapped[int] = mapped_column(
        ForeignKey("diff_hunks.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[str] = mapped_column(String(3))
    # OLD | NEW
    line_number: Mapped[int] = mapped_column(Integer)
    change_type: Mapped[str] = mapped_column(String(10))
    # ADDED | DELETED


class DiffSymbol(Base):
    __tablename__ = "diff_symbols"

    id: Mapped[int] = mapped_column(primary_key=True)
    diff_id: Mapped[int] = mapped_column(
        ForeignKey("diffs.id", ondelete="CASCADE"), index=True
    )
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True
    )
    file_path: Mapped[str] = mapped_column(String(1000))
    symbol_name: Mapped[str] = mapped_column(String(500))
    symbol_kind: Mapped[str] = mapped_column(String(50))
    change_type: Mapped[str] = mapped_column(String(20))
    # ADDED | MODIFIED | DELETED
    added_lines: Mapped[int] = mapped_column(Integer, default=0)
    deleted_lines: Mapped[int] = mapped_column(Integer, default=0)


# --- Phase 6: Change impact simulation ----------------------------------------


class ImpactAnalysis(Base):
    __tablename__ = "impact_analyses"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "diff_id",
            "max_depth",
            name="uq_impact_repo_diff_depth",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    diff_id: Mapped[int] = mapped_column(
        ForeignKey("diffs.id", ondelete="CASCADE"), index=True
    )
    max_depth: Mapped[int] = mapped_column(Integer, default=2)
    base_revision: Mapped[str] = mapped_column(String(64))
    head_revision: Mapped[str] = mapped_column(String(64))

    changed_symbols: Mapped[int] = mapped_column(Integer, default=0)
    changed_files: Mapped[int] = mapped_column(Integer, default=0)
    total_direct: Mapped[int] = mapped_column(Integer, default=0)
    potential: Mapped[int] = mapped_column(Integer, default=0)
    affected_tests: Mapped[int] = mapped_column(Integer, default=0)
    unresolved_count: Mapped[int] = mapped_column(Integer, default=0)
    external_count: Mapped[int] = mapped_column(Integer, default=0)
    file_level_changes: Mapped[int] = mapped_column(Integer, default=0)
    nodes_total: Mapped[int] = mapped_column(Integer, default=0)
    paths_total: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    truncated_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ImpactNode(Base):
    __tablename__ = "impact_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("impact_analyses.id", ondelete="CASCADE"), index=True
    )
    node_type: Mapped[str] = mapped_column(String(20))
    # SYMBOL | FILE | UNRESOLVED | EXTERNAL
    node_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(1000))
    kind: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    file_category: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    impact_class: Mapped[str] = mapped_column(String(20))
    # DIRECT | POTENTIAL | UNRESOLVED | EXTERNAL
    depth: Mapped[int] = mapped_column(Integer, default=0)
    line_start: Mapped[int] = mapped_column(Integer, default=0)
    line_end: Mapped[int] = mapped_column(Integer, default=0)
    via_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    via_source: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    evidence_file: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    evidence_line: Mapped[int] = mapped_column(Integer, default=0)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    is_file_level_change: Mapped[bool] = mapped_column(Boolean, default=False)
    change_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # ADDED | MODIFIED | DELETED (changed symbols only)


class ImpactPath(Base):
    __tablename__ = "impact_paths"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("impact_analyses.id", ondelete="CASCADE"), index=True
    )
    root: Mapped[str] = mapped_column(String(1000))
    target: Mapped[str] = mapped_column(String(1000))
    target_node_type: Mapped[str] = mapped_column(String(20))
    target_node_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[str] = mapped_column(Text, default="[]")  # JSON


# --- Phase 7: Deterministic change review workflow -----------------------------


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    diff_id: Mapped[int] = mapped_column(
        ForeignKey("diffs.id", ondelete="CASCADE"), index=True
    )
    impact_analysis_id: Mapped[int] = mapped_column(
        ForeignKey("impact_analyses.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    # OPEN | IN_PROGRESS | DONE | SKIPPED
    base_revision: Mapped[str] = mapped_column(String(64))
    head_revision: Mapped[str] = mapped_column(String(64))
    max_depth: Mapped[int] = mapped_column(Integer, default=2)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    required_count: Mapped[int] = mapped_column(Integer, default=0)
    recommended_count: Mapped[int] = mapped_column(Integer, default=0)
    informational_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        UniqueConstraint(
            "review_id", "stable_key", name="uq_review_item_stable_key"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), index=True
    )
    stable_key: Mapped[str] = mapped_column(String(500))
    item_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    # OPEN | IN_PROGRESS | DONE | SKIPPED
    priority: Mapped[str] = mapped_column(String(20))
    # REQUIRED | RECOMMENDED | INFORMATIONAL
    source_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    source_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    evidence_file: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    evidence_start_line: Mapped[int] = mapped_column(Integer, default=0)
    evidence_end_line: Mapped[int] = mapped_column(Integer, default=0)
    group_via: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ReviewItemEntry(Base):
    __tablename__ = "review_item_entries"
    __table_args__ = (
        UniqueConstraint(
            "review_item_id", "entry_key", name="uq_review_item_entry_key"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_item_id: Mapped[int] = mapped_column(
        ForeignKey("review_items.id", ondelete="CASCADE"), index=True
    )
    entry_key: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(30), default="")
    # TEST | CALLER | FILE | UNRESOLVED | EXTERNAL | CONFIG | DOC
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    # OPEN | DONE
    symbol_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    evidence_file: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    evidence_line: Mapped[int] = mapped_column(Integer, default=0)
    path_steps: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# --- Phase 8: Lens AI explanation audit tray ----------------------------------


class LensAudit(Base):
    """Metadata-only audit of every Lens call.

    Stores provider/model/prompt version, a context hash, response status, and
    latency. Deliberately stores no prompts, source snippets, responses, or API
    keys, so the audit tray cannot leak repository content or secrets.
    """

    __tablename__ = "lens_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    # change | impact | review | unresolved
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(20))
    context_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[str] = mapped_column(String(20))
    # ok | provider_error | timeout | invalid_response | no_provider
    error_kind: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

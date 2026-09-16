"""Ingestion service: URL → safe clone → discovery → metadata.

Orchestrates the whole pipeline and owns the repository lifecycle in the
database. Blocking Git/filesystem work runs in a thread executor so the event
loop stays responsive; all database work is async and awaited on the caller's
event loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.relationships.engine import RelationshipEngine
from app.analysis.service import AnalysisService
from app.config import get_settings
from app.core.locks import protected
from app.models.orm import FileRecord, Repository
from app.repositories import acquisition
from app.repositories.cleanup import safe_remove_workspace
from app.repositories.discovery import discover_files
from app.repositories.errors import (
    InternalIngestionError,
    RepositoryIngestionError,
    UnsupportedRepository,
)
from app.repositories.github_url import GitHubRepoRef, parse_github_url

logger = logging.getLogger("repolens.ingestion")


class IngestionService:
    """Handles the end-to-end ingestion of a public GitHub repository."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    # --- public API ----------------------------------------------------------

    async def ingest_from_url(self, url: str) -> Repository:
        repo_ref = parse_github_url(url)
        async with protected(f"ingest:{repo_ref.canonical_url}"):
            return await self._ingest_locked(repo_ref)

    async def _ingest_locked(self, repo_ref: GitHubRepoRef) -> Repository:
        repo_ref = repo_ref
        existing = await self._find_ready(repo_ref.canonical_url)
        if existing is not None:
            logger.info("returning existing analysis for %s", repo_ref.canonical_url)
            await self._complete_analysis(existing)
            return existing

        repository = await self._create_repository(repo_ref)
        workspace = self._workspace_path(repo_ref)
        repository.local_path = str(workspace)
        await self.db.commit()

        try:
            await self._set_status(repository, "cloning")
            branch, commit = await asyncio.to_thread(
                acquisition.acquire_repository,
                repo_ref.owner,
                repo_ref.name,
                workspace,
            )
            repository.branch = branch
            repository.commit_sha = commit

            await self._set_status(repository, "indexing")
            result = await asyncio.to_thread(
                discover_files,
                workspace,
                max_file_size_bytes=self.settings.max_file_size_bytes,
                max_files=self.settings.max_files_per_repo,
                max_repo_bytes=self.settings.max_repo_size_bytes,
            )

            analyzable_language_bytes = result.by_language_bytes()
            if not any(f.analyzable for f in result.files) or not analyzable_language_bytes:
                raise UnsupportedRepository()

            await self._store_files(repository.id, result)
            await self._complete_analysis(repository)
            await self._finalize(repository, result, analyzable_language_bytes)
            logger.info(
                "ingestion complete: %s/%s files=%d",
                repo_ref.owner,
                repo_ref.name,
                repository.file_count,
            )
        except RepositoryIngestionError:
            logger.warning("ingestion failed for %s", repo_ref.canonical_url)
            await self._mark_failed(repository)
            safe_remove_workspace(workspace)
            raise
        except Exception as exc:  # noqa: BLE001 - convert to controlled failure
            logger.exception("unexpected ingestion failure for %s", repo_ref.canonical_url)
            await self._mark_failed(repository)
            safe_remove_workspace(workspace)
            raise InternalIngestionError(cause=exc) from exc

        return repository

    # --- internal helpers ----------------------------------------------------

    async def _find_ready(self, canonical_url: str) -> Optional[Repository]:
        row = (
            await self.db.execute(
                select(Repository).where(Repository.url == canonical_url)
            )
        ).scalar_one_or_none()
        if row is not None and row.status == "ready":
            return row

        # Clear stale rows (failed/pending from earlier runs) for the same URL.
        if row is not None:
            if row.local_path:
                safe_remove_workspace(Path(row.local_path))
            await self.db.delete(row)
            await self.db.commit()
        return None

    async def _create_repository(self, repo_ref: GitHubRepoRef) -> Repository:
        repository = Repository(
            url=repo_ref.canonical_url,
            owner=repo_ref.owner,
            name=repo_ref.name,
            status="pending",
        )
        self.db.add(repository)
        await self.db.commit()
        return repository

    def _workspace_path(self, repo_ref: GitHubRepoRef) -> Path:
        token = secrets.token_hex(3)
        directory = self.settings.repo_storage_dir / (
            f"{repo_ref.owner}__{repo_ref.name}__{token}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    async def _set_status(self, repository: Repository, status: str) -> None:
        repository.status = status
        await self.db.commit()

    async def _store_files(self, repository_id: int, result) -> None:
        rows = [
            {
                "repository_id": repository_id,
                "path": f.relative_path,
                "language": f.language,
                "line_count": f.line_count,
                "size_bytes": f.size_bytes,
                "analyzed": f.analyzable,
            }
            for f in result.files
        ]
        if rows:
            await self.db.execute(sqlite_insert(FileRecord).values(rows))
        await self.db.commit()

    async def _complete_analysis(self, repository: Repository) -> None:
        """Run the deterministic analysis pipeline so "ready" is complete.

        Parse symbols/imports/exports first when needed, then build the
        relationship graph. Both steps are idempotent — parse resets prior
        results and relationship building starts from a clean slate — which
        makes this safe on first ingest and on an existing "ready" repository
        that predates relationship analysis (e.g. it shows symbol search
        results but no relationships yet).
        """
        analysis = AnalysisService(self.db)
        if not repository.analyzed:
            await analysis.analyze_repository(repository)
        if repository.relationship_count == 0:
            engine = RelationshipEngine(self.db)
            await engine.build_relationships(repository.id)

    async def _finalize(
        self, repository: Repository, result, language_bytes: dict[str, int]
    ) -> None:
        total = sum(language_bytes.values()) or 1
        percentages = {
            language: round((size / total) * 100, 1)
            for language, size in sorted(
                language_bytes.items(), key=lambda kv: kv[1], reverse=True
            )
        }
        repository.file_count = len(result.files)
        repository.source_size_bytes = result.total_size_bytes
        repository.languages = json.dumps(percentages)
        repository.status = "ready"
        repository.error_message = None
        await self.db.commit()

    async def _mark_failed(self, repository: Repository) -> None:
        repository.status = "failed"
        await self.db.commit()


async def remove_repository(db: AsyncSession, repository: Repository) -> None:
    """Delete a repository record, all derived data, and its cloned workspace.

    Deletes are bottom-up so every child row is removed before its parent
    (diffs -> diff_files -> hunks/changed_lines/symbols; impact_analyses ->
    nodes/paths; reviews -> items -> entries; files -> symbols/imports/
    exports/parse_results and relationships). Nothing of a removed repository
    is allowed to leak into a different repository's rows.
    """
    rid = repository.id
    if repository.local_path:
        safe_remove_workspace(Path(repository.local_path))
    params = {"rid": rid}

    # Diff graph (Phase 4).
    await db.execute(
        text("DELETE FROM diff_changed_lines WHERE diff_hunk_id IN "
             "(SELECT id FROM diff_hunks WHERE diff_file_id IN "
             "(SELECT id FROM diff_files WHERE diff_id IN "
             "(SELECT id FROM diffs WHERE repository_id = :rid)))"),
        params,
    )
    await db.execute(
        text("DELETE FROM diff_symbols WHERE diff_id IN "
             "(SELECT id FROM diffs WHERE repository_id = :rid)"),
        params,
    )
    await db.execute(
        text("DELETE FROM diff_hunks WHERE diff_file_id IN "
             "(SELECT id FROM diff_files WHERE diff_id IN "
             "(SELECT id FROM diffs WHERE repository_id = :rid))"),
        params,
    )
    await db.execute(
        text("DELETE FROM diff_files WHERE diff_id IN "
             "(SELECT id FROM diffs WHERE repository_id = :rid)"),
        params,
    )
    await db.execute(
        text("DELETE FROM diffs WHERE repository_id = :rid"), params
    )

    # Impact analyses (Phase 6).
    await db.execute(
        text("DELETE FROM impact_paths WHERE analysis_id IN "
             "(SELECT id FROM impact_analyses WHERE repository_id = :rid)"),
        params,
    )
    await db.execute(
        text("DELETE FROM impact_nodes WHERE analysis_id IN "
             "(SELECT id FROM impact_analyses WHERE repository_id = :rid)"),
        params,
    )
    await db.execute(
        text("DELETE FROM impact_analyses WHERE repository_id = :rid"), params
    )

    # Reviews (Phase 7).
    await db.execute(
        text("DELETE FROM review_item_entries WHERE review_item_id IN "
             "(SELECT id FROM review_items WHERE review_id IN "
             "(SELECT id FROM reviews WHERE repository_id = :rid))"),
        params,
    )
    await db.execute(
        text("DELETE FROM review_items WHERE review_id IN "
             "(SELECT id FROM reviews WHERE repository_id = :rid)"),
        params,
    )
    await db.execute(
        text("DELETE FROM reviews WHERE repository_id = :rid"), params
    )

    # Lens audits (Phase 8).
    await db.execute(text("DELETE FROM lens_audits WHERE repository_id = :rid"), params)

    # Parsed analysis artifacts (Phase 3/5).
    await db.execute(
        text("DELETE FROM relationships WHERE repository_id = :rid"), params
    )
    for table in ("parse_results", "symbols", "imports", "exports"):
        await db.execute(
            text(
                f"DELETE FROM {table} WHERE file_id IN "
                "(SELECT id FROM files WHERE repository_id = :rid)"
            ),
            params,
        )
    await db.execute(delete(FileRecord).where(FileRecord.repository_id == rid))
    await db.delete(repository)
    await db.commit()
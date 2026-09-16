"""Relationship engine: orchestrates all resolvers and persists results.

Runs the deterministic relationship pipeline for a repository:
load → define → imports → exports → calls → references → inheritance → tests → persist.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.relationships.call_resolver import resolve_calls
from app.analysis.relationships.defines_resolver import resolve_defines
from app.analysis.relationships.export_resolver import resolve_exports
from app.analysis.relationships.index import RelationshipIndex, load_index
from app.analysis.relationships.import_resolver import resolve_imports
from app.analysis.relationships.inheritance_resolver import resolve_inheritance
from app.analysis.relationships.models import RelationshipEdge
from app.analysis.relationships.reference_resolver import resolve_references
from app.analysis.relationships.test_resolver import resolve_tests
from app.models.orm import Relationship, Repository

logger = logging.getLogger("repolens.relationships.engine")

MAX_RELATIONSHIPS_PER_REPOSITORY = 100_000


class RelationshipEngine:
    """Orchestrates relationship resolution for a single repository."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_relationships(self, repository_id: int) -> dict:
        """Run the full relationship pipeline and persist results."""
        start = time.perf_counter()

        repository = await self.db.get(Repository, repository_id)
        if repository is None:
            raise LookupError(f"repository {repository_id} not found")
        if not repository.analyzed:
            raise RuntimeError(
                f"repository {repository_id} has not been analyzed yet; run parse first"
            )

        await self._reset(repository_id)
        await self.db.commit()

        index = await load_index(self.db, repository_id)

        repo_root = repository.local_path

        all_edges: list[RelationshipEdge] = []
        all_edges.extend(resolve_defines(index))
        all_edges.extend(resolve_imports(index))
        all_edges.extend(resolve_exports(index))
        all_edges.extend(resolve_calls(index, repo_root))
        all_edges.extend(resolve_inheritance(index, repo_root))
        all_edges.extend(resolve_references(index, repo_root))
        all_edges.extend(resolve_tests(index, repo_root))

        # Collapse edges that would collide on the table's unique constraint
        # (e.g. two imports of different targets on one line -> no crash).
        all_edges = _dedupe_edges(all_edges)

        if len(all_edges) > MAX_RELATIONSHIPS_PER_REPOSITORY:
            logger.warning(
                "truncated relationships from %d to %d for repo %d",
                len(all_edges),
                MAX_RELATIONSHIPS_PER_REPOSITORY,
                repository_id,
            )
            all_edges = all_edges[:MAX_RELATIONSHIPS_PER_REPOSITORY]

        await self._persist(repository_id, all_edges, index)
        await self.db.commit()

        duration_ms = int((time.perf_counter() - start) * 1000)

        counts = {"RESOLVED": 0, "UNRESOLVED": 0, "EXTERNAL": 0}
        for edge in all_edges:
            counts[edge.resolution_status] = counts.get(edge.resolution_status, 0) + 1

        repository.relationship_count = len(all_edges)
        await self.db.commit()

        return {
            "repository_id": repository_id,
            "status": "ready",
            "relationships_created": len(all_edges),
            "resolved": counts["RESOLVED"],
            "external": counts["EXTERNAL"],
            "unresolved": counts["UNRESOLVED"],
            "duration_ms": duration_ms,
        }

    async def _reset(self, repository_id: int) -> None:
        """Delete existing relationships for idempotent re-runs."""
        await self.db.execute(
            Relationship.__table__.delete().where(
                Relationship.repository_id == repository_id
            )
        )

    async def _persist(
        self, repository_id: int, edges: list[RelationshipEdge], index: RelationshipIndex
    ) -> None:
        """Persist relationship edges to the database."""
        for edge in edges:
            self.db.add(
                Relationship(
                    repository_id=repository_id,
                    source_symbol_id=edge.source_symbol_id,
                    target_symbol_id=edge.target_symbol_id,
                    type=edge.type,
                    resolution_status=edge.resolution_status,
                    source_type=edge.source_type,
                    target_type=edge.target_type,
                    evidence_file_id=edge.evidence_file_id,
                    evidence_start_line=edge.evidence_start_line,
                    evidence_end_line=edge.evidence_end_line,
                    evidence=edge.evidence,
                    target_file=edge.target_file,
                )
            )


def _dedupe_edges(edges: list[RelationshipEdge]) -> list[RelationshipEdge]:
    """Drop edges that share the database uniqueness key.

    Keeps the first occurrence and preserves order. Mirrors the
    ``uq_relationship_edge`` constraint so degenerate source (two different
    imports starting on one line) cannot abort the whole relationship run.
    """
    seen: set[tuple] = set()
    unique: list[RelationshipEdge] = []
    for edge in edges:
        key = (
            edge.source_symbol_id,
            edge.target_symbol_id,
            edge.type,
            edge.resolution_status,
            edge.evidence_file_id,
            edge.evidence_start_line,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(edge)
    return unique

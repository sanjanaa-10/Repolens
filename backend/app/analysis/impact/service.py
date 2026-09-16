"""Impact read service: renders persisted impact analyses into API schemas."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import ImpactAnalysis, ImpactNode, ImpactPath
from app.models.schemas import (
    ImpactAnalysisInfo,
    ImpactNodeInfo,
    ImpactPathInfo,
    ImpactStepInfo,
    ImpactSummary,
)

# Deterministic response ordering. The engine persists nodes in traversal order
# but the response is always re-assembled with these keys, so POST and GET
# return byte-identical bodies.


def _changed_key(node: ImpactNode) -> tuple:
    # symbols before files; alphabetical within each
    return (
        0 if node.node_type == "SYMBOL" else 1,
        node.file_path or "",
        node.name or "",
        node.id,
    )


def _potential_key(node: ImpactNode) -> tuple:
    return (node.depth, node.file_path or "", node.name or "", node.id)


def _unresolved_key(node: ImpactNode) -> tuple:
    return (node.file_path or "", node.evidence_line, node.name or "", node.id)


def _external_key(node: ImpactNode) -> tuple:
    return (node.file_path or "", node.name or "", node.id)


class ImpactNotFoundError(LookupError):
    pass


async def get_impact_analysis(
    db: AsyncSession, repository_id: int, analysis_id: int
) -> ImpactAnalysis:
    result = await db.execute(
        select(ImpactAnalysis).where(
            ImpactAnalysis.id == analysis_id,
            ImpactAnalysis.repository_id == repository_id,
        )
    )
    analysis = result.scalar_one_or_none()
    if analysis is None:
        raise ImpactNotFoundError(f"impact analysis {analysis_id} not found")
    return analysis


def _node_to_info(node: ImpactNode) -> ImpactNodeInfo:
    return ImpactNodeInfo(
        node_type=node.node_type,
        node_id=node.node_id,
        name=node.name,
        kind=node.kind,
        file_path=node.file_path,
        file_category=node.file_category,
        impact_class=node.impact_class,
        depth=node.depth,
        line_start=node.line_start,
        line_end=node.line_end,
        via=node.via_type,
        via_source=node.via_source,
        evidence_file=node.evidence_file,
        evidence_line=node.evidence_line,
        is_test=node.is_test,
        is_file_level_change=node.is_file_level_change,
        change_type=node.change_type,
    )


async def analysis_to_info(
    db: AsyncSession, analysis: ImpactAnalysis
) -> ImpactAnalysisInfo:
    nodes = list(
        (
            await db.execute(
                select(ImpactNode)
                .where(ImpactNode.analysis_id == analysis.id)
                .order_by(ImpactNode.id)
            )
        ).scalars().all()
    )
    paths = list(
        (
            await db.execute(
                select(ImpactPath)
                .where(ImpactPath.analysis_id == analysis.id)
                .order_by(ImpactPath.id)
            )
        ).scalars().all()
    )

    changed = sorted((n for n in nodes if n.impact_class == "DIRECT"), key=_changed_key)
    potential = sorted(
        (n for n in nodes if n.impact_class == "POTENTIAL" and not n.is_test),
        key=_potential_key,
    )
    tests = sorted(
        (n for n in nodes if n.impact_class == "POTENTIAL" and n.is_test),
        key=_potential_key,
    )
    unresolved_nodes = sorted(
        (n for n in nodes if n.impact_class == "UNRESOLVED"), key=_unresolved_key
    )
    external_nodes = sorted(
        (n for n in nodes if n.impact_class == "EXTERNAL"), key=_external_key
    )

    path_infos = [
        ImpactPathInfo(
            root=p.root,
            target=p.target,
            target_node_type=p.target_node_type,
            target_node_id=p.target_node_id,
            depth=p.depth,
            steps=[
                ImpactStepInfo(**step) for step in json.loads(p.steps or "[]")
            ],
        )
        for p in sorted(paths, key=lambda p: (p.depth, p.target, p.id))
    ]

    summary = ImpactSummary(
        changed_symbols=analysis.changed_symbols,
        changed_files=analysis.changed_files,
        direct=analysis.total_direct,
        potential=analysis.potential,
        affected_tests=analysis.affected_tests,
        unresolved=analysis.unresolved_count,
        external=analysis.external_count,
        file_level_changes=analysis.file_level_changes,
        nodes_total=analysis.nodes_total,
        truncated=analysis.truncated,
        truncated_reason=analysis.truncated_reason,
    )

    return ImpactAnalysisInfo(
        analysis_id=analysis.id,
        repository_id=analysis.repository_id,
        diff_id=analysis.diff_id,
        base_revision=analysis.base_revision,
        head_revision=analysis.head_revision,
        max_depth=analysis.max_depth,
        summary=summary,
        changed=[_node_to_info(n) for n in changed],
        potentially_affected=[_node_to_info(n) for n in potential],
        tests=[_node_to_info(n) for n in tests],
        unresolved=[_node_to_info(n) for n in unresolved_nodes],
        external=[_node_to_info(n) for n in external_nodes],
        paths=path_infos,
    )
"""Bounded, deterministic Lens context builders (Phase 8).

Every Lens call sends a small, numbered set of VERIFIED evidence drawn from
RepoLens' deterministic analysis, plus a scope/summary block that references
existing identifiers (diff_id, analysis_id, review_id) — never source content.

The model explains and selects from this list by index; it is never asked to
invent or search for more. Context sizes are capped by budget so a Lens request
stays small even for large diffs or analyses.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.diff.service import get_diff_files, get_diff_hunks, get_diff_symbols
from app.analysis.impact.service import analysis_to_info, get_impact_analysis
from app.analysis.review.service import get_review, review_to_info
from app.analysis.service import AnalysisService
from app.config import Settings
from app.core.redaction import redact_text
from app.models.orm import Diff, DiffFile, ImpactNode, Repository
from app.models.schemas import LensEvidenceInfo
from app.repositories.discovery import PathEscapeError

MAX_IMPACT_NODES_PER_KIND = 30
MAX_REVIEW_EXPANSION = 3

# Hard cap on the number of evidence items materialized into any Lens context,
# independent of the character budget. Larger contexts are sliced before the
# provider payload is built so a pathological repository cannot force an
# unbounded in-memory list. Truncation is surfaced via evidence_truncated.
MAX_EVIDENCE_ITEMS = 100


class DiffFileNotFoundError(LookupError):
    pass


class LensContextUnavailableError(RuntimeError):
    pass


def _status_value(value: Any) -> str:
    """Accept either the ORM plain string or a Pydantic str enum."""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


@dataclass
class LensContext:
    """A snapshot of deterministic evidence for one Lens call."""

    kind: str  # change | impact | review | unresolved
    goal: str
    scope: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    evidence: list[LensEvidenceInfo] = field(default_factory=list)

    def render(self, settings: Settings) -> dict[str, Any]:
        """Truncated, JSON-serializable payload sent to the provider."""
        budget = settings.lens_max_context_chars
        max_snippet = settings.lens_max_source_snippet_chars
        payload: dict[str, Any] = {
            "kind": self.kind,
            "scope": self.scope,
            "summary": self.summary,
            "evidence": [],
        }
        used = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        for ev in self.evidence:
            if len(payload["evidence"]) >= MAX_EVIDENCE_ITEMS:
                payload["evidence_truncated"] = True
                break
            entry: dict[str, Any] = {
                "id": ev.index,
                "kind": ev.kind,
                "label": redact_text(ev.label),
            }
            if ev.impact:
                entry["impact"] = ev.impact
            if ev.file:
                entry["file"] = redact_text(ev.file)
            if ev.line is not None:
                entry["line"] = ev.line
            if ev.detail:
                entry["detail"] = redact_text(ev.detail)
            snippet = redact_text(ev.snippet or "")
            if snippet:
                entry["snippet"] = snippet[:max_snippet]
            entry_json = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
            if used + len(entry_json) + 2 > budget:
                payload["evidence_truncated"] = True
                break
            payload["evidence"].append(entry)
            used += len(entry_json) + 2
        payload["evidence_truncated"] = payload.get("evidence_truncated", False)
        return payload

    def digest(self, settings: Settings) -> str:
        text = json.dumps(
            self.render(settings),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_hunk_lines(content: str) -> list[tuple[str, int, str]]:
    """Parse unified diff text into ``(side, line_number, text)`` tuples."""
    parsed: list[tuple[str, int, str]] = []
    old_line = 0
    new_line = 0
    for raw in content.splitlines():
        if not raw:
            continue
        marker = raw[0]
        body = raw[1:]
        if marker == "+":
            new_line += 1
            parsed.append(("NEW", new_line, body))
        elif marker == "-":
            old_line += 1
            parsed.append(("OLD", old_line, body))
        elif marker == " ":
            new_line += 1
            old_line += 1
    return parsed


def _format_changed_lines(parsed: list[tuple[str, int, str]]) -> str:
    """Render unified changed lines as ``+ 12 text`` / ``- 8 text`` strings."""
    return "\n".join(
        f"{'+' if side == 'NEW' else '-'} {line_number}: {text}"
        for side, line_number, text in parsed
    )


async def _snippet(
    analysis: AnalysisService,
    repository: Repository,
    path: str | None,
    line_start: int,
    line_end: int,
) -> str | None:
    """Read a bounded source snippet around the evidence, or ``None``.

    Path traversal is blocked by the same ``safe_join`` used by the parse
    pipeline; content is decoded defensively (never executed).
    """
    if not path:
        return None
    try:
        raw = await analysis.read_source(repository, path)
    except (OSError, OverflowError, PathEscapeError):
        return None
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if line_start < 1:
        line_start = 1
    if line_end < line_start or line_start > len(lines):
        return None
    line_end = min(line_end, len(lines))
    return "\n".join(lines[line_start - 1 : line_end])


async def _impact_rows(db: AsyncSession, analysis_id: int) -> dict[tuple[str, str, str], ImpactNode]:
    """Map deterministic identity -> ImpactNode row to expose stable row ids."""
    rows = (
        (
            await db.execute(
                select(ImpactNode).where(ImpactNode.analysis_id == analysis_id)
            )
        )
        .scalars()
        .all()
    )
    return {(n.impact_class, n.node_type, n.name): n for n in rows}


# --- Kind builders ------------------------------------------------------------


async def build_change_context(
    db: AsyncSession,
    repository: Repository,
    diff: Diff,
    diff_file_id: int | None,
    settings: Settings,
) -> LensContext:
    files = await get_diff_files(db, diff.id)
    symbols = await get_diff_symbols(db, diff.id)
    analysis = AnalysisService(db)

    scope = {
        "diff_id": diff.id,
        "base_revision": diff.base_revision,
        "head_revision": diff.head_revision,
    }
    summary = {
        "files_changed": diff.files_changed,
        "insertions": diff.insertions,
        "deletions": diff.deletions,
        "symbols_changed": diff.symbols_changed,
    }

    evidence: list[LensEvidenceInfo] = []
    index = 0

    def add(
        kind: str,
        impact: str,
        label: str,
        *,
        file: str | None = None,
        detail: str | None = None,
        line: int | None = None,
        snippet: str | None = None,
        symbol_id: int | None = None,
    ) -> None:
        nonlocal index
        index += 1
        evidence.append(
            LensEvidenceInfo(
                index=index,
                kind=kind,
                impact=impact,
                label=label,
                file=file,
                detail=detail,
                line=line,
                snippet=snippet,
                symbol_id=symbol_id,
            )
        )

    if diff_file_id is not None:
        target = next((f for f in files if f.id == diff_file_id), None)
        if target is None:
            raise DiffFileNotFoundError(f"diff file {diff_file_id} not found")
        target_status = _status_value(target.status)
        add(
            "changed-file",
            "DIRECT",
            f"{target_status} {target.path}",
            file=target.path,
            detail=f"{target_status} · +{target.additions} −{target.deletions}",
        )
        hunks = await get_diff_hunks(db, target.id)
        for hunk in hunks:
            changed = _parse_hunk_lines(hunk.content)
            lines_text = _format_changed_lines(changed) if changed else None
            snippet = await _snippet(
                analysis,
                repository,
                target.path,
                hunk.new_start,
                hunk.new_start + hunk.new_count - 1,
            )
            if not lines_text:
                lines_text = snippet
            add(
                "diff-hunk",
                "DIRECT",
                f"Hunk {hunk.header}",
                file=target.path,
                detail=lines_text,
                line=hunk.new_start,
                snippet=snippet,
            )
        for symbol in symbols:
            if symbol.file_path != target.path:
                continue
            add(
                "changed-symbol",
                "DIRECT",
                f"{symbol.change_type} {symbol.symbol_kind} {symbol.symbol_name}",
                file=symbol.file_path,
                detail=f"{symbol.change_type} · +{symbol.added_lines} −{symbol.deleted_lines}",
                symbol_id=symbol.symbol_id,
            )
    else:
        for file in files:
            status = _status_value(file.status)
            add(
                "changed-file",
                "DIRECT",
                f"{status} {file.path}",
                file=file.path,
                detail=f"{status} · +{file.additions} −{file.deletions}",
            )
        for symbol in symbols:
            add(
                "changed-symbol",
                "DIRECT",
                f"{symbol.change_type} {symbol.symbol_kind} {symbol.symbol_name}",
                file=symbol.file_path,
                detail=f"{symbol.change_type} · +{symbol.added_lines} −{symbol.deleted_lines}",
                symbol_id=symbol.symbol_id,
            )

    return LensContext(
        kind="change",
        goal=(
            "Explain what this diff changes, which symbols or files are touched, "
            "and how the change relates to the rest of the repository."
        ),
        scope=scope,
        summary=summary,
        evidence=evidence,
    )


async def build_impact_context(
    db: AsyncSession,
    repository: Repository,
    analysis_id: int,
    settings: Settings,
) -> LensContext:
    analysis = await get_impact_analysis(db, repository.id, analysis_id)
    info = await analysis_to_info(db, analysis)
    rows = await _impact_rows(db, analysis.id)
    analysis_service = AnalysisService(db)
    max_paths = settings.lens_max_impact_paths_in_context

    scope = {
        "analysis_id": analysis.id,
        "diff_id": analysis.diff_id,
        "base_revision": analysis.base_revision,
        "head_revision": analysis.head_revision,
        "max_depth": analysis.max_depth,
    }
    summary = {
        "changed_symbols": info.summary.changed_symbols,
        "changed_files": info.summary.changed_files,
        "direct": info.summary.direct,
        "potential": info.summary.potential,
        "affected_tests": info.summary.affected_tests,
        "unresolved": info.summary.unresolved,
        "external": info.summary.external,
        "file_level_changes": info.summary.file_level_changes,
        "truncated": info.summary.truncated,
    }

    evidence: list[LensEvidenceInfo] = []
    index = 0

    def add(
        kind: str,
        impact: str,
        label: str,
        *,
        file: str | None = None,
        detail: str | None = None,
        line: int | None = None,
        snippet: str | None = None,
        row: ImpactNode | None = None,
    ) -> None:
        nonlocal index
        index += 1
        evidence.append(
            LensEvidenceInfo(
                index=index,
                kind=kind,
                impact=impact,
                label=label,
                file=file,
                detail=detail,
                line=line,
                snippet=snippet,
                node_id=row.id if row else None,
                symbol_id=row.node_id if row else None,
                depth=row.depth if row else None,
            )
        )

    for node in info.changed[:MAX_IMPACT_NODES_PER_KIND]:
        row = rows.get(("DIRECT", node.node_type, node.name))
        snippet = await _snippet(
            analysis_service, repository, node.file_path, node.line_start, node.line_end
        )
        add(
            "changed-symbol" if node.node_type == "SYMBOL" else "changed-file",
            "DIRECT",
            node.name,
            file=node.file_path,
            detail=f"{node.change_type or 'changed'} · {node.kind or 'unknown'}",
            line=node.line_start or None,
            snippet=snippet,
            row=row,
        )

    for node in info.potentially_affected[:MAX_IMPACT_NODES_PER_KIND]:
        row = rows.get(("POTENTIAL", node.node_type, node.name))
        snippet = await _snippet(
            analysis_service,
            repository,
            node.evidence_file or node.file_path,
            node.evidence_line,
            node.evidence_line,
        )
        add(
            "potentially-affected",
            "POTENTIAL",
            node.name,
            file=node.file_path,
            detail=f"via {node.via or 'reference'} · depth {node.depth}",
            line=node.evidence_line or None,
            snippet=snippet,
            row=row,
        )

    for node in info.tests[:MAX_IMPACT_NODES_PER_KIND]:
        row = rows.get(("POTENTIAL", node.node_type, node.name))
        snippet = await _snippet(
            analysis_service,
            repository,
            node.evidence_file or node.file_path,
            node.evidence_line,
            node.evidence_line,
        )
        add(
            "test",
            "POTENTIAL",
            node.name,
            file=node.file_path,
            detail=f"test · via {node.via or 'TESTS'}",
            line=node.evidence_line or None,
            snippet=snippet,
            row=row,
        )

    for node in info.external[:10]:
        row = rows.get(("EXTERNAL", node.node_type, node.name))
        snippet = await _snippet(
            analysis_service,
            repository,
            node.evidence_file or node.file_path,
            node.evidence_line,
            node.evidence_line,
        )
        add(
            "external",
            "EXTERNAL",
            node.name,
            file=node.file_path,
            detail="external dependency",
            line=node.evidence_line or None,
            snippet=snippet,
            row=row,
        )

    for path in info.paths[:max_paths]:
        add(
            "impact-path",
            "POTENTIAL",
            f"{path.root} → {path.target}",
            file=path.steps[-1].evidence.split(":")[0] if path.steps else None,
            detail=f"call chain · {len(path.steps)} hop(s)",
            line=(int(path.steps[-1].evidence.split(":")[1]) if path.steps and ":" in path.steps[-1].evidence else None),
            row=None,
        )

    return LensContext(
        kind="impact",
        goal=(
            "Explain which parts of the repository this change could affect, "
            "how the call/reference chains work, and how confident a developer "
            "should be that each item is really impacted."
        ),
        scope=scope,
        summary=summary,
        evidence=evidence,
    )


async def build_review_context(
    db: AsyncSession,
    repository: Repository,
    review_id: int,
    settings: Settings,
) -> LensContext:
    review = await get_review(db, repository.id, review_id)
    info = await review_to_info(db, review)
    analysis_service = AnalysisService(db)

    scope = {
        "review_id": review.id,
        "impact_analysis_id": review.impact_analysis_id,
        "diff_id": review.diff_id,
        "base_revision": review.base_revision,
        "head_revision": review.head_revision,
    }
    summary = {
        "total_items": info.review_summary.total_items,
        "required": info.review_summary.required,
        "recommended": info.review_summary.recommended,
        "informational": info.review_summary.informational,
        "completed": info.review_summary.completed,
        "open": info.review_summary.open,
    }

    evidence: list[LensEvidenceInfo] = []
    index = 0

    def add(
        kind: str,
        impact: str,
        label: str,
        *,
        file: str | None = None,
        detail: str | None = None,
        snippet: str | None = None,
        item_id: int | None = None,
        symbol_id: int | None = None,
    ) -> None:
        nonlocal index
        index += 1
        evidence.append(
            LensEvidenceInfo(
                index=index,
                kind=kind,
                impact=impact,
                label=label,
                file=file,
                detail=detail,
                snippet=snippet,
                item_id=item_id,
                symbol_id=symbol_id,
            )
        )

    for item in info.items:
        snippet = await _snippet(
            analysis_service,
            repository,
            item.evidence_file,
            item.evidence_start_line,
            item.evidence_end_line,
        )
        add(
            "review-item",
            item.priority.value,
            f"[{item.priority.value}] {item.title}",
            file=item.evidence_file,
            detail=(item.description or "")[:300] or None,
            snippet=snippet,
            item_id=item.item_id,
        )
        if item.item_type.value == "UNRESOLVED_IMPACT":
            for entry in item.entries[:MAX_REVIEW_EXPANSION]:
                add(
                    "review-entry",
                    "UNRESOLVED",
                    entry.title,
                    file=entry.file_path or entry.evidence_file,
                    detail=(entry.description or "")[:200] or None,
                    snippet=await _snippet(
                        analysis_service,
                        repository,
                        entry.evidence_file or entry.file_path,
                        entry.evidence_line,
                        entry.evidence_line,
                    ),
                    item_id=item.item_id,
                    symbol_id=entry.symbol_id,
                )

    return LensContext(
        kind="review",
        goal=(
            "Explain why each checklist item matters for this change, what the "
            "reviewer should verify, and prioritize the most important checks."
        ),
        scope=scope,
        summary=summary,
        evidence=evidence,
    )


async def build_unresolved_context(
    db: AsyncSession,
    repository: Repository,
    analysis_id: int,
    settings: Settings,
) -> LensContext:
    analysis = await get_impact_analysis(db, repository.id, analysis_id)
    info = await analysis_to_info(db, analysis)
    rows = await _impact_rows(db, analysis.id)
    analysis_service = AnalysisService(db)
    max_paths = settings.lens_max_impact_paths_in_context

    scope = {
        "analysis_id": analysis.id,
        "diff_id": analysis.diff_id,
        "base_revision": analysis.base_revision,
        "head_revision": analysis.head_revision,
    }
    summary = {"unresolved": info.summary.unresolved}

    unresolved_rows = {
        (n.node_type, n.name): rows.get(("UNRESOLVED", n.node_type, n.name))
        for n in info.unresolved
    }
    unresolved_target_ids = {
        row.id for row in unresolved_rows.values() if row is not None
    }

    evidence: list[LensEvidenceInfo] = []
    index = 0

    for node in info.unresolved:
        row = unresolved_rows.get((node.node_type, node.name))
        if row is not None:
            unresolved_target_ids.add(row.id)
        snippet = await _snippet(
            analysis_service,
            repository,
            node.evidence_file or node.file_path,
            node.evidence_line,
            node.evidence_line,
        )
        index += 1
        evidence.append(
            LensEvidenceInfo(
                index=index,
                kind="unresolved-call",
                impact="UNRESOLVED",
                label=node.name,
                file=node.evidence_file or node.file_path,
                detail=(
                    f"Dynamic call target that RepoLens could not resolve "
                    f"(evidence {node.evidence_file}:{node.evidence_line})"
                ),
                line=node.evidence_line or None,
                snippet=snippet,
                node_id=row.id if row else None,
                symbol_id=row.node_id if row else None,
            )
        )

    for path in info.paths[:max_paths]:
        if path.target_node_id not in unresolved_target_ids:
            continue
        index += 1
        evidence.append(
            LensEvidenceInfo(
                index=index,
                kind="impact-path",
                impact="UNRESOLVED",
                label=f"{path.root} → {path.target}",
                detail="chain ending at an unresolved call target",
                node_id=path.target_node_id,
                depth=path.depth,
            )
        )

    return LensContext(
        kind="unresolved",
        goal=(
            "Explain why these call targets could not be resolved statically "
            "(dynamic dispatch, string-based imports, etc.) and exactly what a "
            "developer should verify manually before relying on them."
        ),
        scope=scope,
        summary=summary,
        evidence=evidence,
    )
"""Deterministic change-impact engine (Phase 6).

Consumes Phase 5 diff objects (changed symbols/files) plus Phase 3
repository relationships and produces a bounded, explainable impact analysis:

* DIRECT      - the changed entity itself
* POTENTIAL   - reachable through resolved, evidence-bearing relationship edges
* UNRESOLVED  - observed relationships from a changed entity that could not be
                statically resolved (with location and reason)
* EXTERNAL    - resolved external (out-of-repository) imports from changed files

No risk scores, no LLM, no git access during traversal. The engine is
deterministic: same inputs produce identical output (and identical persisted
rows), so POST and GET agree byte-for-byte.
"""
from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.diff.classifier import classify_file
from app.analysis.diff.service import get_diff, get_diff_files, get_diff_symbols
from app.analysis.impact.limits import (
    MAX_IMPACT_EXTERNAL_NODES,
    MAX_IMPACT_NODES,
    MAX_IMPACT_PATHS,
    MAX_IMPACT_UNRESOLVED_NODES,
    MAX_RELATIONSHIPS_PER_REPOSITORY,
)
from app.analysis.impact.policy import (
    CONTAINER_KINDS,
    TRAVERSED_RELATIONSHIP_TYPES,
    edge_priority,
)
from app.analysis.impact.service import analysis_to_info
from app.core.locks import protected
from app.models.orm import (
    FileRecord,
    ImpactAnalysis,
    ImpactNode,
    ImpactPath,
    Relationship,
    Symbol,
)
from app.models.schemas import ImpactAnalysisInfo

logger = logging.getLogger(__name__)

# tuple keys into the registry
SYMBOL_KEY = 0
FILE_KEY = 1


def _relationship_sort_key(edge: Relationship) -> tuple:
    return (
        edge_priority(edge.type),
        edge.evidence_file_id or 0,
        edge.evidence_start_line,
        edge.id,
    )


class ImpactEngine:
    def __init__(self, db: AsyncSession, repository_id: int, diff_id: int, max_depth: int):
        self.db = db
        self.repository_id = repository_id
        self.diff_id = diff_id
        self.max_depth = max_depth

        self.max_nodes = MAX_IMPACT_NODES
        self.max_paths = MAX_IMPACT_PATHS

        self._diff: Any | None = None
        self._fid_to_path: dict[int, str] = {}
        self._path_to_fid: dict[str, int] = {}
        self._symbols: dict[int, Symbol] = {}

        self._incoming: dict[int, list[Relationship]] = {}
        self._file_imports: dict[str, list[Relationship]] = {}
        self._unresolved_edges: list[Relationship] = []
        self._external_edges: list[Relationship] = []

        self.registry: dict[tuple, dict[str, Any]] = {}
        self.pred: dict[tuple, tuple[tuple, Relationship]] = {}
        self.queue: deque = deque()
        self._truncated = False
        self._truncate_reason: str | None = None

    # ------------------------------------------------------------------ loads
    async def _load_context(self) -> None:
        rows = (
            await self.db.execute(
                select(FileRecord).where(FileRecord.repository_id == self.repository_id)
            )
        ).scalars().all()
        self._fid_to_path = {f.id: f.path for f in rows}
        self._path_to_fid = {f.path: f.id for f in rows}

        syms = (
            await self.db.execute(
                select(Symbol).where(Symbol.repository_id == self.repository_id)
            )
        ).scalars().all()
        self._symbols = {s.id: s for s in syms}

        resolved = (
            await self.db.execute(
                select(Relationship).where(
                    Relationship.repository_id == self.repository_id,
                    Relationship.resolution_status == "RESOLVED",
                    Relationship.target_symbol_id.isnot(None),
                )
            )
        ).scalars().all()
        if len(resolved) > MAX_RELATIONSHIPS_PER_REPOSITORY:
            logger.warning(
                "relationship edges exceed %d; traversing a bounded subset",
                MAX_RELATIONSHIPS_PER_REPOSITORY,
            )
            resolved = resolved[:MAX_RELATIONSHIPS_PER_REPOSITORY]
        for edge in resolved:
            self._incoming.setdefault(edge.target_symbol_id, []).append(edge)
        for edges in self._incoming.values():
            edges.sort(key=_relationship_sort_key)

        file_to_file = (
            await self.db.execute(
                select(Relationship).where(
                    Relationship.repository_id == self.repository_id,
                    Relationship.resolution_status == "RESOLVED",
                    Relationship.source_type == "file",
                    Relationship.target_symbol_id.is_(None),
                    Relationship.target_file.isnot(None),
                )
            )
        ).scalars().all()
        for edge in file_to_file:
            self._file_imports.setdefault(edge.target_file, []).append(edge)
        for edges in self._file_imports.values():
            edges.sort(key=_relationship_sort_key)

        self._unresolved_edges = list(
            (
                await self.db.execute(
                    select(Relationship).where(
                        Relationship.repository_id == self.repository_id,
                        Relationship.resolution_status == "UNRESOLVED",
                    )
                )
            ).scalars().all()
        )
        self._external_edges = list(
            (
                await self.db.execute(
                    select(Relationship).where(
                        Relationship.repository_id == self.repository_id,
                        Relationship.resolution_status == "EXTERNAL",
                    )
                )
            ).scalars().all()
        )

    def _file_path(self, file_id: int | None) -> str | None:
        if file_id is None:
            return None
        return self._fid_to_path.get(file_id)

    def _symbol_name(self, symbol: Symbol) -> str:
        return symbol.qualified_name or symbol.name

    # ------------------------------------------------------------ node registry
    def _register(self, key: tuple, spec: dict[str, Any]) -> bool:
        if key in self.registry:
            return False
        if len(self.registry) >= self.max_nodes:
            self._truncated = True
            self._truncate_reason = "impact node limit reached"
            return False
        self.registry[key] = spec
        return True

    # ----------------------------------------------------------- traversal core
    def _expand(self, key: tuple, depth: int) -> None:
        spec = self.registry[key]
        if spec["node_type"] == "SYMBOL":
            symbol = self._symbols.get(spec["node_id"])
            target_ids = [spec["node_id"]]
            if (
                symbol
                and symbol.parent_symbol_id
                and symbol.parent_symbol_id != spec["node_id"]
            ):
                parent = self._symbols.get(symbol.parent_symbol_id)
                if parent and parent.kind in CONTAINER_KINDS:
                    target_ids.append(parent.id)
            for target_id in target_ids:
                for edge in self._incoming.get(target_id, []):
                    if edge.type not in TRAVERSED_RELATIONSHIP_TYPES:
                        continue
                    self._try_add_edge(edge, depth, key)
        elif spec["node_type"] == "FILE" and spec.get("is_file_level_change"):
            for edge in self._file_imports.get(spec["file_path"], []):
                if edge.type not in TRAVERSED_RELATIONSHIP_TYPES:
                    continue
                self._try_add_edge(edge, depth, key)

    def _try_add_edge(self, edge: Relationship, depth: int, parent_key: tuple) -> None:
        if edge.source_type == "file":
            file_id = edge.evidence_file_id
            path = self._file_path(file_id)
            if path is None:
                return
            key = (FILE_KEY, path)
            category = classify_file(path)
            is_test = edge.type == "TESTS" or category == "TEST"
            spec = {
                "node_type": "FILE",
                "node_id": file_id,
                "name": path,
                "kind": "FILE",
                "file_path": path,
                "file_category": category,
                "impact_class": "POTENTIAL",
                "depth": depth + 1,
                "line_start": 0,
                "line_end": 0,
                "via_type": edge.type,
                "via_source": path,
                "evidence_file": path,
                "evidence_line": edge.evidence_start_line,
                "is_test": is_test,
                "is_file_level_change": False,
                "change_type": None,
            }
        else:
            source_id = edge.source_symbol_id
            if source_id is None:
                return
            symbol = self._symbols.get(source_id)
            if symbol is None:
                return
            key = (SYMBOL_KEY, source_id)
            file_path = self._file_path(symbol.file_id)
            spec = {
                "node_type": "SYMBOL",
                "node_id": source_id,
                "name": self._symbol_name(symbol),
                "kind": symbol.kind,
                "file_path": file_path,
                "file_category": classify_file(file_path) if file_path else "UNKNOWN",
                "impact_class": "POTENTIAL",
                "depth": depth + 1,
                "line_start": symbol.line_start,
                "line_end": symbol.line_end,
                "via_type": edge.type,
                "via_source": self._symbol_name(symbol),
                "evidence_file": self._file_path(edge.evidence_file_id),
                "evidence_line": edge.evidence_start_line,
                "is_test": False,
                "is_file_level_change": False,
                "change_type": None,
            }

        if len(self.registry) >= self.max_nodes:
            if key not in self.registry:
                self._truncated = True
                self._truncate_reason = "impact node limit reached"
            return

        if self._register(key, spec):
            self.pred[key] = (parent_key, edge)
            self.queue.append((key, depth + 1))

    # ------------------------------------------------------------ roots + seeds
    async def _seed_roots(self) -> tuple[list, list, int]:
        diff_symbols = await get_diff_symbols(self.db, self.diff_id)
        diff_files = await get_diff_files(self.db, self.diff_id)
        changed_symbol_rows = sorted(
            diff_symbols, key=lambda s: (s.file_path, s.symbol_name, s.symbol_id)
        )
        changed_file_rows = sorted(diff_files, key=lambda f: f.path)
        mapped_paths = {s.file_path for s in diff_symbols}

        for ds in changed_symbol_rows:
            symbol = self._symbols.get(ds.symbol_id)
            if symbol is None:
                continue
            file_path = self._file_path(symbol.file_id) or ds.file_path
            spec = {
                "node_type": "SYMBOL",
                "node_id": ds.symbol_id,
                "name": self._symbol_name(symbol),
                "kind": symbol.kind,
                "file_path": file_path,
                "file_category": classify_file(file_path),
                "impact_class": "DIRECT",
                "depth": 0,
                "line_start": symbol.line_start,
                "line_end": symbol.line_end,
                "via_type": None,
                "via_source": None,
                "evidence_file": None,
                "evidence_line": 0,
                "is_test": False,
                "is_file_level_change": False,
                "change_type": ds.change_type,
            }
            if self._register((SYMBOL_KEY, ds.symbol_id), spec):
                self.queue.append(((SYMBOL_KEY, ds.symbol_id), 0))

        file_level_changes = 0
        for df in changed_file_rows:
            file_id = df.new_file_id or df.old_file_id
            if file_id is None:
                file_id = self._path_to_fid.get(df.path)
            fl_change = df.path not in mapped_paths
            if fl_change:
                file_level_changes += 1
            spec = {
                "node_type": "FILE",
                "node_id": file_id,
                "name": df.path,
                "kind": "FILE",
                "file_path": df.path,
                "file_category": classify_file(df.path),
                "impact_class": "DIRECT",
                "depth": 0,
                "line_start": 0,
                "line_end": 0,
                "via_type": None,
                "via_source": None,
                "evidence_file": None,
                "evidence_line": 0,
                "is_test": False,
                "is_file_level_change": fl_change,
                "change_type": None,
            }
            if self._register((FILE_KEY, df.path), spec):
                self.queue.append(((FILE_KEY, df.path), 0))
        return changed_symbol_rows, changed_file_rows, file_level_changes

    # ---------------------------------------------- unresolved + external rows
    def _build_unresolved_and_external(
        self, root_sym_ids: set[int], root_file_ids: set[int]
    ) -> tuple[list[ImpactNode], list[ImpactNode]]:
        unresolved: list[ImpactNode] = []
        seen: set[tuple] = set()
        for edge in sorted(
            self._unresolved_edges,
            key=lambda e: (
                e.source_symbol_id or 0,
                e.evidence_file_id or 0,
                e.type,
                e.evidence_start_line,
            ),
        ):
            if len(unresolved) >= MAX_IMPACT_UNRESOLVED_NODES:
                if not self._truncated:
                    self._truncated = True
                    self._truncate_reason = "unresolved node limit reached"
                break
            scoped = False
            if edge.source_symbol_id is not None and edge.source_symbol_id in root_sym_ids:
                scoped = True
            elif (
                edge.source_type == "file"
                and edge.evidence_file_id is not None
                and edge.evidence_file_id in root_file_ids
            ):
                scoped = True
            if not scoped:
                continue
            key = (
                edge.source_symbol_id,
                edge.evidence_file_id,
                edge.type,
                edge.evidence_start_line,
            )
            if key in seen:
                continue
            seen.add(key)
            evidence_path = self._file_path(edge.evidence_file_id)
            name = edge.target_file or edge.evidence or f"{edge.type} @ {edge.evidence_start_line}"
            unresolved.append(
                ImpactNode(
                    node_type="UNRESOLVED",
                    node_id=None,
                    name=str(name),
                    kind=f"UNRESOLVED-{edge.type}",
                    file_path=evidence_path,
                    file_category=classify_file(evidence_path) if evidence_path else "UNKNOWN",
                    impact_class="UNRESOLVED",
                    depth=0,
                    line_start=0,
                    line_end=0,
                    via_type=edge.type,
                    via_source=str(edge.evidence or name),
                    evidence_file=evidence_path,
                    evidence_line=edge.evidence_start_line,
                    is_test=False,
                    is_file_level_change=False,
                    change_type=None,
                )
            )

        external: list[ImpactNode] = []
        seen_ext: set[tuple] = set()
        for edge in sorted(
            self._external_edges,
            key=lambda e: (
                e.target_file or "",
                e.evidence_file_id or 0,
                e.evidence_start_line,
            ),
        ):
            if len(external) >= MAX_IMPACT_EXTERNAL_NODES:
                if not self._truncated:
                    self._truncated = True
                    self._truncate_reason = "external node limit reached"
                break
            if edge.evidence_file_id not in root_file_ids:
                continue
            key = (edge.target_file, edge.evidence_file_id)
            if key in seen_ext:
                continue
            seen_ext.add(key)
            evidence_path = self._file_path(edge.evidence_file_id)
            name = edge.target_file or edge.evidence or "external"
            external.append(
                ImpactNode(
                    node_type="EXTERNAL",
                    node_id=None,
                    name=str(name),
                    kind="EXTERNAL_IMPORT",
                    file_path=evidence_path,
                    file_category=classify_file(evidence_path) if evidence_path else "UNKNOWN",
                    impact_class="EXTERNAL",
                    depth=0,
                    line_start=0,
                    line_end=0,
                    via_type=edge.type,
                    via_source="external import",
                    evidence_file=evidence_path,
                    evidence_line=edge.evidence_start_line,
                    is_test=False,
                    is_file_level_change=False,
                    change_type=None,
                )
            )
        return unresolved, external

    # ------------------------------------------------------------------ paths
    def _build_paths(self) -> list[ImpactPath]:
        """One explainable path per reached node.

        Each step is rendered in dependency direction: the impacted node
        (child) depends on the changed node (parent) through a relationship,
        evidenced at the dependency site. E.g. for CALLS the step reads
        ``<caller> CALLS <callee> at <caller-file>:<line>``.
        """
        paths: list[ImpactPath] = []
        non_roots = [k for k in self.registry if k in self.pred]
        non_roots.sort(key=lambda k: (self.registry[k]["depth"], self.registry[k]["name"]))
        for key in non_roots:
            if len(paths) >= self.max_paths:
                if not self._truncated:
                    self._truncated = True
                    self._truncate_reason = "impact path limit reached"
                break
            steps: list[dict] = []
            cur = key
            chain: list[tuple] = []  # (parent_key, edge, child_key) root-outward
            while cur in self.pred:
                prev, edge = self.pred[cur]
                chain.append((prev, edge, cur))
                cur = prev
            for parent_key, edge, child_key in reversed(chain):
                child_spec = self.registry[child_key]
                steps.append(
                    {
                        "source": child_spec["name"],
                        "relationship": edge.type,
                        "target": self.registry[parent_key]["name"],
                        "evidence": (
                            f"{child_spec.get('evidence_file') or ''}:"
                            f"{child_spec.get('evidence_line') or 0}"
                        ),
                    }
                )
            target_spec = self.registry[key]
            paths.append(
                ImpactPath(
                    root=self.registry[cur]["name"],
                    target=target_spec["name"],
                    target_node_type=target_spec["node_type"],
                    target_node_id=target_spec["node_id"],
                    depth=target_spec["depth"],
                    steps=json.dumps(steps),
                )
            )
        return paths

    # ---------------------------------------------------------------- execute
    async def run(self) -> ImpactAnalysisInfo:
        self._diff = await get_diff(self.db, self.repository_id, self.diff_id)

        await self._load_context()
        changed_symbols, changed_files, file_level_changes = await self._seed_roots()

        while self.queue:
            if self._truncated:
                break
            key, depth = self.queue.popleft()
            if depth >= self.max_depth:
                continue
            self._expand(key, depth)

        root_sym_ids = {ds.symbol_id for ds in changed_symbols}
        root_file_ids = {
            df.new_file_id or df.old_file_id
            for df in changed_files
            if (df.new_file_id or df.old_file_id) is not None
        }
        unresolved_nodes, external_nodes = self._build_unresolved_and_external(
            root_sym_ids, root_file_ids
        )

        nodes: list[ImpactNode] = []
        for spec in self.registry.values():
            nodes.append(
                ImpactNode(
                    node_type=spec["node_type"],
                    node_id=spec["node_id"],
                    name=spec["name"],
                    kind=spec["kind"],
                    file_path=spec["file_path"],
                    file_category=spec["file_category"],
                    impact_class=spec["impact_class"],
                    depth=spec["depth"],
                    line_start=spec["line_start"],
                    line_end=spec["line_end"],
                    via_type=spec["via_type"],
                    via_source=spec["via_source"],
                    evidence_file=spec["evidence_file"],
                    evidence_line=spec["evidence_line"],
                    is_test=spec["is_test"],
                    is_file_level_change=spec["is_file_level_change"],
                    change_type=spec["change_type"],
                )
            )
        nodes.extend(unresolved_nodes)
        nodes.extend(external_nodes)

        potential = sum(1 for n in nodes if n.impact_class == "POTENTIAL" and not n.is_test)
        tests = sum(1 for n in nodes if n.impact_class == "POTENTIAL" and n.is_test)

        paths = self._build_paths()

        analysis = ImpactAnalysis(
            repository_id=self.repository_id,
            diff_id=self.diff_id,
            max_depth=self.max_depth,
            base_revision=self._diff.base_revision,
            head_revision=self._diff.head_revision,
            changed_symbols=len(changed_symbols),
            changed_files=len(changed_files),
            total_direct=len(changed_symbols) + len(changed_files),
            potential=potential,
            affected_tests=tests,
            unresolved_count=len(unresolved_nodes),
            external_count=len(external_nodes),
            file_level_changes=file_level_changes,
            nodes_total=len(nodes),
            paths_total=len(paths),
            truncated=self._truncated,
            truncated_reason=self._truncate_reason,
        )
        self.db.add(analysis)
        await self.db.flush()
        for node in nodes:
            node.analysis_id = analysis.id
            self.db.add(node)
        for path in paths:
            path.analysis_id = analysis.id
            self.db.add(path)
        await self.db.commit()

        return await analysis_to_info(self.db, analysis)


async def run_impact_analysis(
    db: AsyncSession,
    repository_id: int,
    diff_id: int,
    max_depth: int,
) -> ImpactAnalysisInfo:
    """Run impact analysis; idempotent per (repository, diff, max_depth)."""
    async with protected(f"impact:{repository_id}:{diff_id}:{max_depth}"):
        return await _run_impact_analysis_unlocked(db, repository_id, diff_id, max_depth)


async def _run_impact_analysis_unlocked(
    db: AsyncSession,
    repository_id: int,
    diff_id: int,
    max_depth: int,
) -> ImpactAnalysisInfo:
    """Run impact analysis; idempotent per (repository, diff, max_depth)."""
    existing = (
        await db.execute(
            select(ImpactAnalysis).where(
                ImpactAnalysis.repository_id == repository_id,
                ImpactAnalysis.diff_id == diff_id,
                ImpactAnalysis.max_depth == max_depth,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return await analysis_to_info(db, existing)
    engine = ImpactEngine(db, repository_id, diff_id, max_depth)
    return await engine.run()
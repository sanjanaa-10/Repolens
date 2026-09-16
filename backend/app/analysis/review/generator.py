"""Deterministic review checklist generation from persisted Phase 6 findings.

The generator is a pure function of an ``ImpactAnalysisInfo`` snapshot. It never
touches the filesystem, never re-traverses relationships, and never invokes an
LLM. Every item is derived from verified diff and impact data with a stable
identity key so regeneration can carry reviewer state forward.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.analysis.review.policy import (
    BEHAVIOR_VIA,
    DEPENDENCY_VIA,
    MAX_REVIEW_ENTRIES_PER_ITEM,
    MAX_REVIEW_ITEMS,
    RELATION_LABELS,
    item_sort,
    priority_for_item,
)
from app.core.redaction import clean_text, redact_secret  # noqa: F401
from app.models.schemas import (
    ImpactAnalysisInfo,
    ImpactNodeInfo,
    ImpactStepInfo,
)

__all__ = ["clean_text", "redact_secret"]


@dataclass
class EntrySpec:
    entry_key: str
    title: str
    description: str
    kind: str
    symbol_id: Optional[int] = None
    file_path: Optional[str] = None
    evidence_file: Optional[str] = None
    evidence_line: int = 0
    path_steps: list = field(default_factory=list)
    sort: int = 0


@dataclass
class ItemSpec:
    stable_key: str
    item_type: str
    title: str
    description: str
    priority: str
    source_type: Optional[str] = None
    source_id: Optional[int] = None
    evidence_file: Optional[str] = None
    evidence_start_line: int = 0
    evidence_end_line: int = 0
    group_via: Optional[str] = None
    sort: int = 0
    entries: list = field(default_factory=list)


@dataclass
class ReviewSpec:
    title: str
    summary: str
    items: list = field(default_factory=list)

    @property
    def total_items(self) -> int:
        return len(self.items)

    def count(self, priority: str) -> int:
        return sum(1 for item in self.items if item.priority == priority)


class _PathIndex:
    """Shortest-path lookup keyed by target node name."""

    def __init__(self, info: ImpactAnalysisInfo) -> None:
        self._paths: dict[str, list[ImpactStepInfo]] = {}
        # service responses are already sorted by (depth, target, id); keep the
        # first (shortest) path deterministically even for unsorted callers.
        for path in sorted(info.paths, key=lambda p: (p.depth, p.target)):
            if path.target not in self._paths:
                self._paths[path.target] = path.steps

    def steps_for(self, target: str) -> list:
        steps = self._paths.get(target)
        if steps:
            return _dump_steps(steps)
        return []


def _dump_steps(steps: list[ImpactStepInfo]) -> list:
    dumped = []
    for step in steps:
        if hasattr(step, "model_dump"):
            dumped.append(step.model_dump())
        elif hasattr(step, "dict"):
            dumped.append(step.dict())
        else:
            dumped.append(
                {
                    "source": step.source,
                    "relationship": step.relationship,
                    "target": step.target,
                    "evidence": step.evidence,
                }
            )
    return dumped


def _evidence_file(node: ImpactNodeInfo) -> Optional[str]:
    return node.evidence_file if node.evidence_file else node.file_path


def _cap_entries(entries: list, label: str) -> str:
    """Append a truncation note when an aggregated entry list is capped."""
    if len(entries) <= MAX_REVIEW_ENTRIES_PER_ITEM:
        return ""
    return (
        f" Only the first {MAX_REVIEW_ENTRIES_PER_ITEM} of {len(entries)} {label} "
        "are listed; review the rest from the impact analysis."
    )


def _changed_symbol_items(
    changed: list[ImpactNodeInfo], paths: _PathIndex
) -> list[ItemSpec]:
    items = []
    index = 0
    for node in changed:
        if node.node_type == "FILE":
            continue
        file_path = node.file_path or "<unknown>"
        symbol_key = node.name or "<unnamed>"
        description = (
            f"{node.name} ({node.kind or 'symbol'}) was "
            f"{node.change_type or 'changed'} in {file_path}. "
            "A changed source symbol is the root of the change under review; "
            "verify the change is correct, complete, and covered by tests."
        )
        entry = EntrySpec(
            entry_key=f"{node.name}:{file_path}",
            title=file_path,
            description=clean_text(description),
            kind="FILE",
            symbol_id=node.node_id,
            file_path=file_path,
            evidence_file=_evidence_file(node),
            evidence_line=node.evidence_line,
            path_steps=paths.steps_for(node.name),
            sort=0,
        )
        item = ItemSpec(
            stable_key=f"CHANGED_CODE:{file_path}:{symbol_key}",
            item_type="CHANGED_CODE",
            title=f"Review changes to {node.name}",
            description=clean_text(description),
            priority="REQUIRED",
            source_type=node.node_type,
            source_id=node.node_id,
            evidence_file=_evidence_file(node),
            evidence_start_line=node.line_start,
            evidence_end_line=node.line_end,
            sort=item_sort("CHANGED_CODE", "REQUIRED", index),
            entries=[entry],
        )
        items.append(item)
        index += 1
    return items


def _file_level_items(changed: list[ImpactNodeInfo], paths: _PathIndex) -> list[ItemSpec]:
    config_nodes = []
    doc_nodes = []
    other_nodes = []
    for node in changed:
        if node.node_type != "FILE" or not node.is_file_level_change:
            continue
        category = (node.file_category or "UNKNOWN").upper()
        if category == "CONFIG":
            config_nodes.append(node)
        elif category == "DOCUMENTATION":
            doc_nodes.append(node)
        else:
            other_nodes.append(node)

    items = []

    def _make_grouped(
        item_type: str,
        nodes: list[ImpactNodeInfo],
        label: str,
        priority: str,
        index_start: int,
    ) -> None:
        nonlocal_index = [index_start]
        if not nodes:
            return
        entries = []
        for i, node in enumerate(nodes):
            file_path = node.file_path or "<unknown>"
            entries.append(
                EntrySpec(
                    entry_key=f"FILE:{file_path}",
                    title=node.name or file_path,
                    description=clean_text(
                        f"{node.name or file_path} was {node.change_type or 'changed'} "
                        f"without a symbol-level mapping."
                    ),
                    kind=label,
                    symbol_id=node.node_id,
                    file_path=file_path,
                    evidence_file=_evidence_file(node),
                    evidence_line=node.evidence_line,
                    sort=i,
                )
            )
        entries = entries[:MAX_REVIEW_ENTRIES_PER_ITEM]
        note = _cap_entries(
            [_ for _ in nodes], f"{label.lower()} file(s)"
        )
        description = (
            f"{len(nodes)} file(s) changed without symbol-level mapping. "
            f"Review the change for correctness, side effects, and tests.{note}"
        )
        items.append(
            ItemSpec(
                stable_key=f"{item_type}:FILE_LEVEL",
                item_type=item_type,
                title=f"Review {len(nodes)} file-level change(s)",
                description=clean_text(description),
                priority=priority,
                source_type="FILE",
                source_id=None,
                evidence_file=_evidence_file(nodes[0]),
                evidence_start_line=nodes[0].line_start,
                evidence_end_line=nodes[0].line_end,
                sort=item_sort(item_type, priority, nonlocal_index[0]),
                entries=entries,
            )
        )
        nonlocal_index[0] += 1

    _make_grouped("CONFIGURATION_CHANGE", config_nodes, "CONFIG", "REQUIRED", 0)
    _make_grouped("CHANGED_CODE", other_nodes, "FILE", "REQUIRED", 1)
    _make_grouped("DOCUMENTATION_CHANGE", doc_nodes, "DOC", "INFORMATIONAL", 2)
    return items


def _dependency_items(
    potential: list[ImpactNodeInfo], paths: _PathIndex
) -> list[ItemSpec]:
    # Behavior relationships are split into direct (depth == 1) and transitive
    # (depth > 1) buckets so a REQUIRED group really means "direct dependents",
    # never a mix of direct + deep callers. Non-behavior groups stay whole.
    buckets: dict[str, list[ImpactNodeInfo]] = {}
    for node in potential:
        via = node.via or "NONE"
        if via in BEHAVIOR_VIA:
            key = f"{via}:direct" if node.depth == 1 else f"{via}:transitive"
        else:
            key = via
        buckets.setdefault(key, []).append(node)

    items = []
    index = 0
    for key in sorted(buckets):
        nodes = sorted(
            buckets[key],
            key=lambda n: (n.depth, n.file_path or "", n.name or ""),
        )
        if ":" in key:
            via, level = key.split(":", 1)
            transitive = level == "transitive"
        else:
            via, transitive = key, False
        if via in DEPENDENCY_VIA:
            item_type = "AFFECTED_DEPENDENCY"
            label = RELATION_LABELS.get(via, "dependent files")
        else:
            item_type = "AFFECTED_CALLER"
            label = RELATION_LABELS.get(via, "affected entities")
        all_direct = not transitive
        priority = priority_for_item(item_type, via, all_direct)
        entries = []
        for i, node in enumerate(nodes):
            evidence = f"via {node.via or ''}"
            if node.evidence_file:
                evidence += f" at {node.evidence_file}:{node.evidence_line}"
            entries.append(
                EntrySpec(
                    entry_key=f"{via}:{node.name}:{node.file_path or ''}",
                    title=node.name or node.file_path or "<unknown>",
                    description=clean_text(
                        f"{node.name or node.file_path or '<unknown>'} is "
                        f"{evidence} (depth {node.depth})."
                    ),
                    kind="CALLER" if item_type == "AFFECTED_CALLER" else "FILE",
                    symbol_id=node.node_id,
                    file_path=node.file_path,
                    evidence_file=_evidence_file(node),
                    evidence_line=node.evidence_line,
                    path_steps=paths.steps_for(node.name),
                    sort=i,
                )
            )
        total = len(entries)
        entries = entries[:MAX_REVIEW_ENTRIES_PER_ITEM]
        note = _cap_entries(nodes, f"dependent(s) via {via}")
        if transitive:
            description = (
                f"{total} {label} are reached *transitively* through resolved "
                f"{via} relationships from the changed code (depth > 1). Verify "
                f"they still compile and behave as expected with the change.{note}"
            )
            stable_suffix = "transitive"
            title_label = f"downstream {label}"
        else:
            description = (
                f"{total} {label} are reached through resolved {via} relationships "
                f"from the changed code. Verify they still compile and behave as "
                f"expected with the change.{note}"
            )
            stable_suffix = "direct"
            title_label = label
        items.append(
            ItemSpec(
                stable_key=f"{item_type}:{via}:{stable_suffix}",
                item_type=item_type,
                title=f"Review {total} {title_label} affected through resolved {via} relationships",
                description=clean_text(description),
                priority=priority,
                source_type="IMPACT",
                source_id=None,
                evidence_file=(
                    _evidence_file(nodes[0]) if nodes else None
                ),
                evidence_start_line=nodes[0].line_start if nodes else 0,
                evidence_end_line=nodes[0].line_end if nodes else 0,
                group_via=via,
                sort=item_sort(item_type, priority, index),
                entries=entries,
            )
        )
        index += 1
    return items


def _test_items(tests: list[ImpactNodeInfo], paths: _PathIndex) -> list[ItemSpec]:
    if not tests:
        return []
    entries = []
    for i, node in enumerate(tests):
        file_path = node.file_path or "<unknown>"
        entries.append(
            EntrySpec(
                entry_key=f"TEST:{node.name}:{file_path}",
                title=node.name or file_path,
                description=clean_text(
                    f"Test {node.name or file_path} is related to the changed code; "
                    f"run it and confirm it covers the change."
                ),
                kind="TEST",
                symbol_id=node.node_id,
                file_path=file_path,
                evidence_file=_evidence_file(node),
                evidence_line=node.evidence_line,
                path_steps=paths.steps_for(node.name),
                sort=i,
            )
        )
    return [
        ItemSpec(
            stable_key="AFFECTED_TEST:all",
            item_type="AFFECTED_TEST",
            title=f"Verify {len(tests)} test(s) related to the changed code",
            description=clean_text(
                "The listed test(s) relate to changed symbols through resolved "
                "relationships. Run the relevant suite and confirm the change is "
                "covered before merging."
            ),
            priority="REQUIRED",
            source_type="IMPACT",
            source_id=None,
            evidence_file=_evidence_file(tests[0]),
            evidence_start_line=tests[0].line_start,
            evidence_end_line=tests[0].line_end,
            sort=item_sort("AFFECTED_TEST", "REQUIRED", 0),
            entries=entries[:MAX_REVIEW_ENTRIES_PER_ITEM],
        )
    ]


def _unresolved_item(unresolved: list[ImpactNodeInfo]) -> list[ItemSpec]:
    if not unresolved:
        return []
    entries = []
    for i, node in enumerate(unresolved):
        entries.append(
            EntrySpec(
                entry_key=(
                    f"UNRESOLVED:{node.name}:{node.evidence_file or ''}:{node.evidence_line}"
                ),
                title=node.name or "<unresolved>",
                description=clean_text(
                    f"{node.name or '<unresolved>'} referenced at "
                    f"{node.evidence_file or '?'}:{node.evidence_line} "
                    f"could not be resolved to a repository symbol."
                ),
                kind="UNRESOLVED",
                symbol_id=None,
                file_path=node.file_path,
                evidence_file=node.evidence_file,
                evidence_line=node.evidence_line,
                sort=i,
            )
        )
    total = len(entries)
    entries = entries[:MAX_REVIEW_ENTRIES_PER_ITEM]
    note = _cap_entries(unresolved, "unresolved reference(s)")
    bottle = (
        "Unresolved references are conservative: the engine could not tie them "
        f"to a symbol, so manual confirmation is needed.{note}"
    )
    return [
        ItemSpec(
            stable_key="UNRESOLVED_IMPACT:all",
            item_type="UNRESOLVED_IMPACT",
            title=f"Investigate {total} unresolved reference(s) from the changed code",
            description=clean_text(bottle),
            priority="RECOMMENDED",
            source_type="IMPACT",
            source_id=None,
            evidence_file=(_evidence_file(unresolved[0]) if unresolved else None),
            evidence_start_line=unresolved[0].line_start if unresolved else 0,
            evidence_end_line=unresolved[0].line_end if unresolved else 0,
            sort=item_sort("UNRESOLVED_IMPACT", "RECOMMENDED", 0),
            entries=entries,
        )
    ]


def _external_item(external: list[ImpactNodeInfo]) -> list[ItemSpec]:
    if not external:
        return []
    entries = []
    for i, node in enumerate(external):
        entries.append(
            EntrySpec(
                entry_key=f"EXTERNAL:{node.name}:{node.file_path or ''}",
                title=node.name or "<external>",
                description=clean_text(
                    f"{node.name or '<external>'} is external to this repository "
                    f"(used at {node.evidence_file or '?'}:{node.evidence_line}). "
                    "Verify the usage is compatible with the change and available "
                    "in the target environment."
                ),
                kind="EXTERNAL",
                symbol_id=None,
                file_path=node.file_path,
                evidence_file=node.evidence_file,
                evidence_line=node.evidence_line,
                sort=i,
            )
        )
    total = len(entries)
    entries = entries[:MAX_REVIEW_ENTRIES_PER_ITEM]
    note = _cap_entries(external, "external usage(s)")
    return [
        ItemSpec(
            stable_key="EXTERNAL_DEPENDENCY:all",
            item_type="EXTERNAL_DEPENDENCY",
            title=f"Review {total} external package usage(s)",
            description=clean_text(
                "The listed external packages are used by changed or affected "
                f"code. Confirm the usage is compatible with the change.{note}"
            ),
            priority="RECOMMENDED",
            source_type="IMPACT",
            source_id=None,
            evidence_file=(_evidence_file(external[0]) if external else None),
            evidence_start_line=external[0].line_start if external else 0,
            evidence_end_line=external[0].line_end if external else 0,
            sort=item_sort("EXTERNAL_DEPENDENCY", "RECOMMENDED", 0),
            entries=entries,
        )
    ]


def generate_review_spec(info: ImpactAnalysisInfo) -> ReviewSpec:
    """Build a deterministic review from a persisted impact analysis."""
    paths = _PathIndex(info)
    items: list[ItemSpec] = []
    items.extend(_changed_symbol_items(info.changed, paths))
    items.extend(_file_level_items(info.changed, paths))
    items.extend(_dependency_items(info.potentially_affected, paths))
    items.extend(_test_items(info.tests, paths))
    items.extend(_unresolved_item(info.unresolved))
    items.extend(_external_item(info.external))
    items.sort(key=lambda item: (item.sort, item.stable_key))

    truncated_note = ""
    if len(items) > MAX_REVIEW_ITEMS:
        items = items[:MAX_REVIEW_ITEMS]
        truncated_note = (
            f" The review is capped at {MAX_REVIEW_ITEMS} items; lower-priority "
            "items past the cap are omitted from the checklist but remain visible "
            "in the impact analysis."
        )

    title = f"Change review for {info.head_revision}"
    if not items:
        summary = (
            f"Impact analysis #{info.analysis_id} produced no change-related "
            "findings, so no review items were generated. No further action is "
            "required based on static analysis."
        )
    else:
        required = sum(1 for i in items if i.priority == "REQUIRED")
        recommended = sum(1 for i in items if i.priority == "RECOMMENDED")
        informational = sum(1 for i in items if i.priority == "INFORMATIONAL")
        summary = (
            f"Deterministic review checklist derived from impact analysis "
            f"#{info.analysis_id}: {len(items)} items "
            f"({required} required, {recommended} recommended, "
            f"{informational} informational). Review items are generated "
            "deterministically from verified diff and impact findings; no LLM "
            "is used to decide checklist content or priority. Confirming these "
            "items does not guarantee runtime impact or correctness."
            f"{truncated_note}"
        )
    return ReviewSpec(title=title, summary=clean_text(summary), items=items)
"""Inheritance resolution: detect EXTENDS and IMPLEMENTS relationships.

Reads class definition headers (Python and TypeScript/JavaScript), extracts
base-class expressions, and resolves each one to a repository symbol using
only deterministic evidence:

- dotted qualified names are resolved through the module they point at,
- simple names are resolved through the defining file's imports,
- same-file class definitions,
- then a unique repository-wide match (single candidate is confident).

Everything else is recorded as UNRESOLVED rather than guessed.
"""
from __future__ import annotations

import logging
import os

from app.analysis.relationships.import_resolver import build_import_bindings
from app.analysis.relationships.index import RelationshipIndex, SymbolEntry
from app.analysis.relationships.models import RelationshipEdge

logger = logging.getLogger("repolens.relationships.inheritance")


def resolve_inheritance(
    index: RelationshipIndex, repo_root: str | None = None
) -> list[RelationshipEdge]:
    """Resolve EXTENDS and IMPLEMENTS relationships for classes."""
    edges: list[RelationshipEdge] = []

    for sym in index.symbols:
        if sym.kind != "CLASS":
            continue

        base_classes = _extract_base_classes(sym, index, repo_root)
        if not base_classes:
            continue

        import_bindings = build_import_bindings(index, sym.file_id)

        for base_name, rel_type in base_classes:
            target = _resolve_base(base_name, sym, import_bindings, index)
            if target is not None and target.id != sym.id:
                edges.append(RelationshipEdge(
                    type=rel_type,
                    resolution_status="RESOLVED",
                    source_symbol_id=sym.id,
                    target_symbol_id=target.id,
                    source_type="symbol",
                    target_type="symbol",
                    evidence_file_id=sym.file_id,
                    evidence_start_line=sym.line_start,
                    evidence_end_line=sym.line_end,
                    evidence=f"{sym.name} {rel_type.lower()} {base_name}",
                    target_file=target.file_path,
                ))
            else:
                edges.append(RelationshipEdge(
                    type=rel_type,
                    resolution_status="UNRESOLVED",
                    source_symbol_id=sym.id,
                    target_symbol_id=None,
                    source_type="symbol",
                    target_type="symbol",
                    evidence_file_id=sym.file_id,
                    evidence_start_line=sym.line_start,
                    evidence_end_line=sym.line_end,
                    evidence=f"{sym.name} {rel_type.lower()} {base_name}",
                ))

    return edges


def _extract_base_classes(
    sym, index: RelationshipIndex, repo_root: str | None = None
) -> list[tuple[str, str]]:
    """Extract (base_name, relationship_type) pairs from a class's source.

    Python headers may span multiple lines; TypeScript headers expose
    extends/implements clauses that may carry generics.
    """
    file_entry = index.files_by_path.get(sym.file_path)
    if not file_entry:
        return []

    language = (sym.language or file_entry.language or "").lower()
    is_python = language == "python"
    is_ts_like = language in ("javascript", "jsx", "typescript", "tsx")

    try:
        if not repo_root:
            return []
        full_path = os.path.join(repo_root, sym.file_path)
        if not os.path.isfile(full_path):
            return []

        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if sym.line_start - 1 >= len(lines):
            return []

        if is_python:
            return [
                (name, "EXTENDS")
                for name in _python_base_names(lines, sym.line_start - 1)
            ]
        if is_ts_like:
            return _ts_base_names(lines, sym.line_start - 1)
    except Exception:  # noqa: BLE001 - never let one class break the run
        pass

    return []


def _python_base_names(lines: list[str], start_idx: int) -> list[str]:
    """Base expressions of a Python class header, possibly spanning lines."""
    header = lines[start_idx]
    if "class " not in header:
        return []
    open_idx = header.find("(")
    if open_idx == -1:
        return []

    buffer: list[str] = []
    depth = 0
    seen_open = False
    idx = start_idx
    while idx < len(lines):
        for ch in lines[idx]:
            if ch == "(":
                if not seen_open:
                    seen_open = True
                    depth = 1
                    continue
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and seen_open:
                    return _split_python_bases("".join(buffer))
            if seen_open:
                buffer.append(ch)
        idx += 1
    return []


def _split_python_bases(text: str) -> list[str]:
    base_names = []
    for part in text.split(","):
        part = part.strip()
        if not part or part == "*" or "=" in part:
            continue
        base_names.append(part)
    return base_names


def _ts_base_names(lines: list[str], start_idx: int) -> list[tuple[str, str]]:
    """(name, type) pairs from a single-line TypeScript class header."""
    line = lines[start_idx]
    out: list[tuple[str, str]] = []

    if "extends" in line and " implements " in line:
        after_ext = line.split("extends", 1)[1]
        ext_part, impl_part = after_ext.split(" implements ", 1)
        for name in ext_part.split(","):
            cleaned = _clean_ts_name(name)
            if cleaned:
                out.append((cleaned, "EXTENDS"))
        for name in impl_part.split("{")[0].split(","):
            cleaned = _clean_ts_name(name)
            if cleaned:
                out.append((cleaned, "IMPLEMENTS"))
        return out

    if "extends" in line:
        part = line.split("extends", 1)[1].split("{")[0].split(" implements ")[0]
        for name in part.split(","):
            cleaned = _clean_ts_name(name)
            if cleaned:
                out.append((cleaned, "EXTENDS"))
        return out

    if "implements" in line:
        part = line.split("implements", 1)[1].split("{")[0]
        for name in part.split(","):
            cleaned = _clean_ts_name(name)
            if cleaned:
                out.append((cleaned, "IMPLEMENTS"))
        return out

    return out


def _clean_ts_name(name: str) -> str:
    name = name.split("<")[0].strip().strip(";")
    if not name or name == "implements":
        return ""
    return name


def _resolve_base(
    base_name: str,
    class_sym: SymbolEntry,
    import_bindings: dict[str, SymbolEntry],
    index: RelationshipIndex,
) -> SymbolEntry | None:
    """Resolve a base expression to a symbol, or None when not confident."""
    name = base_name.strip()
    if not name:
        return None

    language = (class_sym.language or "").lower()
    allowed = {"CLASS", "INTERFACE"} if language in (
        "javascript", "jsx", "typescript", "tsx"
    ) else {"CLASS"}

    # 1. Dotted qualified name -> resolve through the module it points at.
    if "." in name:
        module_part, cls_name = name.rsplit(".", 1)
        file_entry = index.resolve_module_to_file(module_part.strip("."))
        if file_entry is not None:
            for s in index.symbols_by_file.get(file_entry.id, []):
                if s.name == cls_name and s.kind in allowed:
                    return s
        candidates = [
            s for s in index.symbols_by_name.get(cls_name, []) if s.kind in allowed
        ]
        if len(candidates) == 1:
            return candidates[0]
        return None

    # 2. Import binding (the defining file imports this base name).
    if name in import_bindings:
        s = import_bindings[name]
        if s.kind in allowed:
            return s

    # 3. Same-file definition (excludes the class itself).
    file_candidates = [
        s for s in index.symbols_by_name.get(name, [])
        if s.kind in allowed and s.file_id == class_sym.file_id and s.id != class_sym.id
    ]
    if len(file_candidates) == 1:
        return file_candidates[0]
    if len(file_candidates) > 1:
        return None

    # 4. Unique repository-wide match is confident enough.
    global_candidates = [
        s for s in index.symbols_by_name.get(name, []) if s.kind in allowed
    ]
    if len(global_candidates) == 1:
        return global_candidates[0]

    return None
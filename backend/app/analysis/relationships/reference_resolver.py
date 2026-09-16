"""Reference resolution: detect REFERENCE relationships for symbol usage.

Only creates a REFERENCE when the symbol can be resolved confidently from
direct evidence:

- the name is bound by an import in the same file (resolved to the target
  file's own symbols), or
- the name is defined in the same file.

Each source line is attributed to its innermost owning symbol so a class does
not "reference" names that are only used by its methods, and method names do
not appear as references on their own definition lines. Naive global matching
is intentionally absent: an unresolved name produces no edge at all.
"""
from __future__ import annotations

import logging
import os
import re

from app.analysis.relationships.import_resolver import build_import_bindings
from app.analysis.relationships.index import RelationshipIndex, SymbolEntry
from app.analysis.relationships.models import RelationshipEdge

logger = logging.getLogger("repolens.relationships.references")

_PY_IDENTIFIER = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_JS_IDENTIFIER = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\b")

_PYTHON_KEYWORDS = frozenset({
    "if", "for", "while", "with", "def", "class", "return", "import", "from",
    "elif", "else", "try", "except", "raise", "and", "or", "not", "in", "is",
    "lambda", "yield", "assert", "del", "print", "True", "False", "None",
    "self", "cls", "pass", "break", "continue", "global", "nonlocal",
})

_JS_KEYWORDS = frozenset({
    "function", "const", "let", "var", "new", "async", "await", "export",
    "default", "switch", "case", "break", "continue", "if", "else", "for",
    "while", "do", "return", "throw", "try", "catch", "finally", "class",
    "extends", "super", "this", "typeof", "instanceof", "void", "delete",
    "in", "of", "with", "yield", "import", "from", "as", "static",
})


def resolve_references(
    index: RelationshipIndex, repo_root: str | None = None
) -> list[RelationshipEdge]:
    """Create REFERENCE relationships for confidently-resolvable symbol usage."""
    edges: list[RelationshipEdge] = []

    for file_entry in index.files:
        is_python = file_entry.language and file_entry.language.lower() == "python"
        keywords = _PYTHON_KEYWORDS if is_python else _JS_KEYWORDS
        pattern = _PY_IDENTIFIER if is_python else _JS_IDENTIFIER

        syms_in_file = [
            s for s in index.symbols_by_file.get(file_entry.id, [])
            if s.kind != "IMPORT"
        ]
        if not syms_in_file:
            continue

        full_path = os.path.join(repo_root, file_entry.path) if repo_root else None
        if not full_path or not os.path.isfile(full_path):
            continue

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        except OSError:
            continue

        lines = source.split("\n")
        import_bindings = build_import_bindings(index, file_entry.id)
        names_in_file: dict[str, list[SymbolEntry]] = {}
        for s in syms_in_file:
            names_in_file.setdefault(s.name, []).append(s)

        owners = _innermost_owners(lines, syms_in_file)
        seen_by_owner: dict[int, set[str]] = {}

        for line_idx, owner in enumerate(owners):
            if owner is None:
                continue
            line = lines[line_idx]
            if not line.strip():
                continue
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue

            seen = seen_by_owner.setdefault(owner.id, set())
            for match in pattern.finditer(line):
                name = match.group(1)
                if name in keywords or name in seen:
                    continue
                if len(name) < 2:
                    continue
                seen.add(name)

                target = _resolve_reference_target(
                    name, owner, line_idx + 1, import_bindings, names_in_file
                )
                if target is None or target.id == owner.id:
                    continue
                edges.append(RelationshipEdge(
                    type="REFERENCES",
                    resolution_status="RESOLVED",
                    source_symbol_id=owner.id,
                    target_symbol_id=target.id,
                    source_type="symbol",
                    target_type="symbol",
                    evidence_file_id=file_entry.id,
                    evidence_start_line=line_idx + 1,
                    evidence_end_line=line_idx + 1,
                    evidence=f"{owner.name} references {name}",
                ))

    return edges


def _innermost_owners(
    lines: list[str], syms_in_file: list[SymbolEntry]
) -> list[SymbolEntry | None]:
    """Attribute each source line to its innermost owning symbol.

    A symbol owns lines ``line_start <= index <= line_end - 1`` in 0-based
    indexing, i.e. everything below its definition header through its body.
    Lines with no owner (module top-level, definition headers) return None.
    """
    owners: list[SymbolEntry | None] = [None] * len(lines)
    header_lines = {s.line_start - 1 for s in syms_in_file}
    intervals = [
        (s.line_start, s.line_end - 1, s)
        for s in syms_in_file
    ]
    for line_idx in range(len(lines)):
        if line_idx in header_lines:
            continue
        best: SymbolEntry | None = None
        best_start = -1
        best_end = 1 << 30
        for start, end, sym in intervals:
            if start <= line_idx <= end:
                if sym.line_start > best_start or (
                    sym.line_start == best_start and sym.line_end < best_end
                ):
                    best = sym
                    best_start = sym.line_start
                    best_end = sym.line_end
        owners[line_idx] = best
    return owners


def _resolve_reference_target(
    name: str,
    owner: SymbolEntry,
    token_line: int,
    import_bindings: dict[str, SymbolEntry],
    names_in_file: dict[str, list[SymbolEntry]],
):
    """Resolve a name to a target symbol, or None when not confident."""
    if name in import_bindings:
        return import_bindings[name]

    candidates = [c for c in names_in_file.get(name, []) if c.id != owner.id]
    if not candidates:
        return None

    # Same-file names resolve to a symbol whose range contains the token line
    # (an approximation of lexical scoping); otherwise the definition line
    # itself must not be the token's line, or this is a forward/other use.
    in_scope = [c for c in candidates if c.line_start - 1 <= token_line - 1 <= c.line_end - 1]
    if in_scope:
        return in_scope[0]
    return candidates[0]
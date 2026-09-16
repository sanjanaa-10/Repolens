"""Import resolution: map import statements to internal files and symbols.

Resolves both Python (absolute, relative, dotted) and JavaScript/TypeScript
(ES modules, CommonJS require) imports to repository-internal targets where
possible. Unresolvable imports are classified as EXTERNAL or UNRESOLVED.
"""
from __future__ import annotations

import logging
from pathlib import PurePosixPath

from app.analysis.kinds import ImportKind
from app.analysis.relationships.index import (
    ImportEntry,
    RelationshipIndex,
    SymbolEntry,
)
from app.analysis.relationships.models import RelationshipEdge

logger = logging.getLogger("repolens.relationships.imports")

# Extension candidates for JS/TS module resolution
_JS_EXT_CANDIDATES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_JS_INDEX_CANDIDATES = ("/index.ts", "/index.tsx", "/index.js", "/index.jsx")


def _is_relative_import(source: str) -> bool:
    return source.startswith(".")


def _resolve_python_import(
    source: str,
    importing_file_path: str,
    index: RelationshipIndex,
) -> tuple[str | None, str]:
    """Resolve a Python import source to a target file path and status.

    Returns (target_file_path_or_none, resolution_status).
    """
    source = source.strip()

    if _is_relative_import(source):
        dots = 0
        for ch in source:
            if ch == ".":
                dots += 1
            else:
                break
        module_part = source[dots:]
        module_part = module_part.lstrip(".")

        importing_dir = PurePosixPath(importing_file_path).parent
        if dots > 1:
            for _ in range(dots - 1):
                importing_dir = importing_dir.parent

        if module_part:
            candidate_module = str(importing_dir / module_part).replace("/", ".")
        else:
            candidate_module = str(importing_dir).replace("/", ".")

        candidate_module = candidate_module.strip(".")
    else:
        candidate_module = source

    # Resolve module to file
    file_entry = index.resolve_module_to_file(candidate_module)
    if file_entry:
        return file_entry.path, "RESOLVED"

    # Try one level up (the source might be a symbol from a package)
    parts = candidate_module.rsplit(".", 1)
    if len(parts) == 2:
        file_entry = index.resolve_module_to_file(parts[0])
        if file_entry:
            return file_entry.path, "RESOLVED"

    # A non-relative import that is not in the repository is an external
    # package (e.g. `import fastapi`); a relative import that cannot be
    # resolved is ambiguous and classified as UNRESOLVED.
    status = "EXTERNAL" if not _is_relative_import(source) else "UNRESOLVED"
    return None, status


def _resolve_js_import(
    source: str,
    importing_file_path: str,
    index: RelationshipIndex,
) -> tuple[str | None, str]:
    """Resolve a JavaScript/TypeScript import source to a target file path."""
    if not _is_relative_import(source):
        return None, "EXTERNAL"

    importing_dir = PurePosixPath(importing_file_path).parent
    base = importing_dir / source

    # Try exact path first
    for candidate in (str(base), str(base) + ".ts", str(base) + ".tsx",
                      str(base) + ".js", str(base) + ".jsx"):
        entry = index.files_by_path.get(candidate)
        if entry:
            return candidate, "RESOLVED"

    # Try index files
    for idx in _JS_INDEX_CANDIDATES:
        entry = index.files_by_path.get(str(base) + idx)
        if entry:
            return str(base) + idx, "RESOLVED"
        entry = index.files_by_path.get(str(base) + "/index" + PurePosixPath(idx).suffix)
        if entry:
            return str(base) + "/index" + PurePosixPath(idx).suffix, "RESOLVED"

    return None, "UNRESOLVED"


def _find_import_target_symbols(
    imported_name: str | None,
    target_file_path: str,
    index: RelationshipIndex,
) -> list[int]:
    """Find symbol IDs in the target file that match an imported name."""
    if not imported_name or imported_name == "*":
        return []

    file_entry = index.files_by_path.get(target_file_path)
    if not file_entry:
        return []

    syms = index.symbols_by_file.get(file_entry.id, [])
    matches = []
    for sym in syms:
        if sym.name == imported_name:
            matches.append(sym.id)
    return matches


def resolve_import_targets(
    index: RelationshipIndex,
    imp: ImportEntry,
) -> tuple[str | None, str, list[int]]:
    """Resolve an import to (target_path, status, matching symbol IDs).

    Symbol matching is scoped to the *resolved target file*: only symbols
    actually exported from the file the import points at are considered, so
    a name collision in an unrelated file can never hijack the resolution.
    """
    if imp.kind in ("python_import", "python_from_import"):
        target_path, status = _resolve_python_import(imp.source, imp.file_path, index)
    else:
        target_path, status = _resolve_js_import(imp.source, imp.file_path, index)

    if status != "RESOLVED" or not target_path:
        return None, status, []

    sym_ids = []
    if imp.imported_name and imp.imported_name != "*":
        sym_ids = _find_import_target_symbols(imp.imported_name, target_path, index)

    # ES-module default / namespace imports record imported_name=None. If the
    # target file exports exactly one function or class, that is the sole
    # plausible binding for the local name.
    if not sym_ids and imp.kind == "es_module" and imp.imported_name is None and imp.alias:
        sym_ids = _unique_exported_symbols(target_path, index)

    return target_path, status, sym_ids


def _unique_exported_symbols(target_file_path: str, index: RelationshipIndex) -> list[int]:
    """Return the single exported FUNCTION/CLASS symbol of a file, if unique."""
    file_entry = index.files_by_path.get(target_file_path)
    if file_entry is None:
        return []

    candidates: list[int] = []
    for sym in index.symbols_by_file.get(file_entry.id, []):
        if sym.kind not in ("FUNCTION", "ASYNC_FUNCTION", "ARROW_FUNCTION", "CLASS"):
            continue
        if sym.exported:
            candidates.append(sym.id)

    if len(candidates) == 1:
        return candidates
    return []


def build_import_bindings(
    index: RelationshipIndex, file_id: int
) -> dict[str, SymbolEntry]:
    """Map each locally-bound import name to its resolved target symbol.

    Only imports that resolve to an actual symbol are included. A name is
    never bound to the "first global match" - the target file's own symbols
    are the source of truth.
    """
    bindings: dict[str, SymbolEntry] = {}
    for imp in index.imports_by_file.get(file_id, []):
        local_name = imp.alias or imp.imported_name
        if not local_name and imp.kind == "python_import":
            local_name = imp.source.split(".", 1)[0]
        if not local_name or local_name == "*":
            continue

        _, _, sym_ids = resolve_import_targets(index, imp)
        target = None
        for sym_id in sym_ids:
            entry = index.symbols_by_id.get(sym_id)
            if entry is not None:
                target = entry
                break
        if target is not None:
            bindings[local_name] = target
    return bindings


def resolve_imports(index: RelationshipIndex) -> list[RelationshipEdge]:
    """Resolve all imports across the repository."""
    edges: list[RelationshipEdge] = []

    for imp in index.imports:
        target_path, status, target_sym_ids = resolve_import_targets(index, imp)

        if target_sym_ids:
            for sym_id in target_sym_ids:
                edges.append(RelationshipEdge(
                    type="IMPORTS",
                    resolution_status=status,
                    source_symbol_id=None,
                    target_symbol_id=sym_id,
                    source_type="file",
                    target_type="symbol",
                    evidence_file_id=imp.file_id,
                    evidence_start_line=imp.start_line,
                    evidence_end_line=imp.end_line,
                    evidence=f"{imp.source} import {imp.imported_name or '*'}",
                    target_file=target_path,
                ))
        else:
            edges.append(RelationshipEdge(
                type="IMPORTS",
                resolution_status=status,
                source_symbol_id=None,
                target_symbol_id=None,
                source_type="file",
                target_type="file" if target_path else "import",
                evidence_file_id=imp.file_id,
                evidence_start_line=imp.start_line,
                evidence_end_line=imp.end_line,
                evidence=f"{imp.source} import {imp.imported_name or '*'}",
                target_file=target_path,
            ))

    return edges

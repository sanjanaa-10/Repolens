"""Test relationship detection: identify TESTS relationships conservatively.

A TESTS edge is created only when there is direct, deterministic evidence:
a test file imports an application symbol and a test body actually uses that
binding. Targets are resolved through the import binding (the target file's
own symbols), never through name similarity.
"""
from __future__ import annotations

import logging
import os
import re

from app.analysis.relationships.import_resolver import build_import_bindings
from app.analysis.relationships.index import RelationshipIndex
from app.analysis.relationships.models import RelationshipEdge

logger = logging.getLogger("repolens.relationships.tests")

_TEST_DIR_MARKERS = frozenset({"tests", "test", "__tests__"})
_TEST_FILE_PATTERN = re.compile(
    r"^test_.*\.py$|.*_test\.py$|^test_.*\.(js|ts|tsx|jsx)$|.*\.test\.(js|ts|tsx|jsx)$"
)

_PY_IDENTIFIER = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_JS_IDENTIFIER = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\b")

_KEYWORDS = frozenset({
    "if", "for", "while", "with", "def", "class", "return", "import", "from",
    "elif", "else", "try", "except", "raise", "and", "or", "not", "in", "is",
    "lambda", "yield", "assert", "del", "self", "cls", "pass", "break",
    "continue", "global", "nonlocal", "function", "const", "let", "var",
    "new", "async", "await", "export", "default", "switch", "case", "throw",
    "catch", "finally", "extends", "super", "this", "typeof", "instanceof",
    "void", "delete", "of", "with", "as", "static", "get", "set",
})

_APP_KINDS = frozenset({
    "CLASS", "FUNCTION", "ASYNC_FUNCTION", "ARROW_FUNCTION", "METHOD",
})


def _is_test_file(file_path: str) -> bool:
    """Conservatively determine if a file is a test file."""
    parts = file_path.replace("\\", "/").split("/")
    filename = parts[-1]

    if _TEST_FILE_PATTERN.match(filename):
        return True

    if any(p in _TEST_DIR_MARKERS for p in parts[:-1]):
        if filename.startswith("test_") or filename.endswith("_test.py"):
            return True

    return False


def resolve_tests(
    index: RelationshipIndex, repo_root: str | None = None
) -> list[RelationshipEdge]:
    """Create TESTS relationships for test files that use application symbols."""
    edges: list[RelationshipEdge] = []

    for file_entry in index.files:
        if not _is_test_file(file_entry.path):
            continue

        is_python = file_entry.language and file_entry.language.lower() == "python"
        pattern = _PY_IDENTIFIER if is_python else _JS_IDENTIFIER

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
        if not import_bindings:
            continue

        tested: dict[int, tuple[int, str]] = {}

        file_symbols = index.symbols_by_file.get(file_entry.id, [])
        for sym in file_symbols:
            if sym.kind == "IMPORT":
                continue

            start = sym.line_start
            end = min(sym.line_end, len(lines))
            for line_idx in range(start, end):
                line = lines[line_idx]
                if not line.strip():
                    continue
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue

                for match in pattern.finditer(line):
                    name = match.group(1)
                    if name in _KEYWORDS or len(name) < 2:
                        continue
                    target = import_bindings.get(name)
                    if target is None or target.kind not in _APP_KINDS:
                        continue
                    if target.id in tested:
                        break
                    if target.file_id == file_entry.id:
                        continue
                    tested[target.id] = (line_idx + 1, name)

        for target_id, (line_no, used_name) in sorted(tested.items()):
            target = index.symbols_by_id.get(target_id)
            if target is None:
                continue
            edges.append(RelationshipEdge(
                type="TESTS",
                resolution_status="RESOLVED",
                source_symbol_id=None,
                target_symbol_id=target_id,
                source_type="file",
                target_type="symbol",
                evidence_file_id=file_entry.id,
                evidence_start_line=line_no,
                evidence_end_line=line_no,
                evidence=f"test file uses {used_name}",
                target_file=target.file_path,
            ))

    return edges
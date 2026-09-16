"""Call resolution: detect CALLS relationships from function/method invocations.

Practical static approach. A call is resolved only when there is direct
evidence, and each piece of evidence is narrow by design:

- an import binding (the called name is a locally-bound import),
- a same-file definition (the called name is defined in the same file),
- a receiver-based method call whose receiver resolves to a known class.

Names that cannot be resolved to a repository symbol are recorded as
UNRESOLVED so consumers can see the gap rather than a fabricated edge. Naive
global name matching is intentionally absent.
"""
from __future__ import annotations

import logging
import os
import re

from app.analysis.relationships.import_resolver import build_import_bindings
from app.analysis.relationships.index import RelationshipIndex, SymbolEntry
from app.analysis.relationships.models import RelationshipEdge

logger = logging.getLogger("repolens.relationships.calls")

# Call / method patterns. The negative lookbehinds exclude the definition name
# itself (e.g. "def login():" -> "login(" is the definition, not a call).
_PY_CALL_PATTERN = re.compile(r"(?<!def )(?<!class )\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_PY_METHOD_CALL = re.compile(
    r"(?<!def )(?<!class )\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_JS_CALL_PATTERN = re.compile(
    r"(?<!function )(?<!class )\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\("
)
_JS_METHOD_CALL = re.compile(
    r"(?<!function )(?<!class )\b([A-Za-z_$][A-Za-z0-9_$]*)\.([A-Za-z_$][A-Za-z0-9_$]*)\s*\("
)

_PY_KEYWORDS = frozenset({
    "if", "for", "while", "with", "def", "class", "return", "import", "from",
    "elif", "else", "try", "except", "raise", "and", "or", "not", "in", "is",
    "lambda", "yield", "assert", "del", "print", "True", "False", "None",
    "self", "cls", "pass", "break", "continue", "global", "nonlocal", "super",
})

_JS_KEYWORDS = frozenset({
    "function", "const", "let", "var", "new", "async", "await", "export",
    "default", "switch", "case", "break", "continue", "if", "else", "for",
    "while", "do", "return", "throw", "try", "catch", "finally", "class",
    "extends", "super", "this", "typeof", "instanceof", "void", "delete",
    "in", "of", "with", "yield", "import", "from", "as", "static", "get",
    "set",
})

# Names that are almost certainly runtime/builtin machinery rather than
# repository symbols. Skipping them keeps the UNRESOLVED surface honest.
_PY_BUILTINS = frozenset({
    "len", "range", "str", "int", "float", "bool", "dict", "list", "set",
    "tuple", "type", "isinstance", "getattr", "setattr", "hasattr", "iter",
    "next", "min", "max", "sum", "input", "open", "enumerate", "zip", "map",
    "filter", "sorted", "reversed", "any", "all", "abs", "round", "format",
    "repr", "print", "staticmethod", "classmethod", "property", "object",
    "Exception", "ValueError", "TypeError", "KeyError", "RuntimeError",
    "_", "__name__",
})
_JS_BUILTINS = frozenset({
    "console", "JSON", "Math", "Object", "Array", "String", "Number",
    "Boolean", "parseInt", "parseFloat", "isNaN", "fetch", "setTimeout",
    "setInterval", "clearTimeout", "clearInterval", "Promise", "Symbol",
    "RegExp", "Date", "Error", "TypeError", "require", "module", "exports",
    "window", "document", "process", "undefined", "globalThis", "_",
})


def resolve_calls(
    index: RelationshipIndex, repo_root: str | None = None
) -> list[RelationshipEdge]:
    """Resolve CALLS relationships across the repository."""
    edges: list[RelationshipEdge] = []

    for file_entry in index.files:
        language = (file_entry.language or "").lower()
        if language == "python":
            call_pattern = _PY_CALL_PATTERN
            method_pattern = _PY_METHOD_CALL
            keywords = _PY_KEYWORDS
            builtins = _PY_BUILTINS
        elif language in ("javascript", "jsx", "typescript", "tsx"):
            call_pattern = _JS_CALL_PATTERN
            method_pattern = _JS_METHOD_CALL
            keywords = _JS_KEYWORDS
            builtins = _JS_BUILTINS
        else:
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
        syms_in_file = index.symbols_by_file.get(file_entry.id, [])

        for caller_sym in syms_in_file:
            if caller_sym.kind == "IMPORT":
                continue

            for item, line_no in _find_calls_in_symbol(
                caller_sym, lines, call_pattern, method_pattern, keywords, builtins
            ):
                target = _resolve_call_target(item, caller_sym, import_bindings, index)
                if target is not None:
                    edges.append(RelationshipEdge(
                        type="CALLS",
                        resolution_status="RESOLVED",
                        source_symbol_id=caller_sym.id,
                        target_symbol_id=target.id,
                        source_type="symbol",
                        target_type="symbol",
                        evidence_file_id=file_entry.id,
                        evidence_start_line=line_no,
                        evidence_end_line=line_no,
                        evidence=f"{caller_sym.name} calls {target.name}",
                    ))
                else:
                    name = item[1] if item[0] == "method" else item[1]
                    edges.append(RelationshipEdge(
                        type="CALLS",
                        resolution_status="UNRESOLVED",
                        source_symbol_id=caller_sym.id,
                        target_symbol_id=None,
                        source_type="symbol",
                        target_type="symbol",
                        evidence_file_id=file_entry.id,
                        evidence_start_line=line_no,
                        evidence_end_line=line_no,
                        evidence=f"{caller_sym.name} calls {name}",
                    ))

    return edges


def _find_calls_in_symbol(
    sym,
    lines: list[str],
    call_pattern: re.Pattern,
    method_pattern: re.Pattern,
    keywords: frozenset,
    builtins: frozenset,
) -> list[tuple[tuple[str, str, str | None], int]]:
    """Find call sites inside a symbol's source range.

    Returns ``(item, line_no)`` where ``item`` is ``("call", name, None)``
    for a plain call or ``("method", method, receiver)`` for a method call.
    """
    calls: list[tuple[tuple[str, str, str | None], int]] = []
    start = sym.line_start - 1  # 0-based; the def line is guarded by lookbehinds
    end = min(sym.line_end, len(lines))

    for line_idx in range(start, end):
        line = lines[line_idx]
        if not line.strip():
            continue

        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        # Method calls are recorded first; plain-call matches that fall inside
        # a method match's span are the same site (obj.method() also matches
        # the bare "method(" pattern) and must not be emitted twice.
        method_spans: list[tuple[int, int]] = []
        for match in method_pattern.finditer(line):
            obj = match.group(1)
            method = match.group(2)
            if obj in keywords or method in keywords:
                continue
            method_spans.append(match.span())
            calls.append((("method", method, obj), line_idx + 1))

        for match in call_pattern.finditer(line):
            span_start = match.start()
            if any(ms <= span_start < me for ms, me in method_spans):
                continue
            name = match.group(1)
            if name in keywords or name in builtins:
                continue
            calls.append((("call", name, None), line_idx + 1))

    return calls


def _resolve_call_target(
    item: tuple[str, str, str | None],
    caller_sym,
    import_bindings: dict[str, SymbolEntry],
    index: RelationshipIndex,
):
    """Resolve a call site to a target symbol, or None (mark UNRESOLVED)."""
    kind, name, receiver = item

    if kind == "method":
        return _resolve_method_call(receiver, name, caller_sym, import_bindings, index)

    # Plain call: import binding first, then same-file definition.
    if name in import_bindings:
        return import_bindings[name]

    for sym in index.symbols_by_file.get(caller_sym.file_id, []):
        if sym.kind == "IMPORT":
            continue
        if sym.name == name:
            return sym

    return None


def _resolve_method_call(
    receiver: str | None,
    method: str,
    caller_sym,
    import_bindings: dict[str, SymbolEntry],
    index: RelationshipIndex,
):
    """Resolve ``receiver.method(...)`` when the receiver identifies a class."""
    if receiver is None:
        return None

    if receiver in ("self", "cls", "this", "super"):
        # Same-class method call: look among the caller's own class members.
        class_id = caller_sym.parent_symbol_id
        return _find_member(method, class_id, index)

    receiver_sym = None
    if receiver in import_bindings:
        receiver_sym = import_bindings[receiver]
    else:
        for sym in index.symbols_by_file.get(caller_sym.file_id, []):
            if sym.kind == "IMPORT":
                continue
            if sym.name == receiver:
                receiver_sym = sym
                break

    if receiver_sym is None:
        return None

    if receiver_sym.kind in ("CLASS", "INTERFACE"):
        return _find_member(method, receiver_sym.id, index)

    # Receiver is a plain symbol (e.g. an Arrow/VARIABLE holding a function
    # or a module namespace). Only a module import binding maps to a real
    # file/class; we are intentionally conservative here.
    if receiver_sym.kind == "IMPORT":
        return None
    return None


def _find_member(method: str, class_id: int | None, index: RelationshipIndex):
    """Find a method/field symbol owned by ``class_id`` with the given name."""
    if class_id is None:
        return None
    for child in index.children_by_parent.get(class_id, []):
        if child.name == method and child.kind in (
            "METHOD", "FUNCTION", "ASYNC_FUNCTION", "ARROW_FUNCTION", "VARIABLE"
        ):
            return child
    return None
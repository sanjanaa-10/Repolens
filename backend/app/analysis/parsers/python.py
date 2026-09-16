"""Deterministic Python syntax-structure extraction via tree-sitter.

Extracts functions (sync and async), classes, methods, nested symbols,
imports, source coordinates, and structural signatures. Source is treated as
text only; nothing is imported or executed.
"""
from __future__ import annotations

import logging
from typing import Optional

from tree_sitter import Language, Node, Parser as TSParser, Tree
from tree_sitter_python import language as _python_language

from app.analysis.kinds import ImportKind, ParserStatus, SymbolKind
from app.analysis.models import ImportNode, ParseOutcome, SymbolNode
from app.analysis.parsers.base import Parser, _ptr_to_loc, _slice, _version_of

logger = logging.getLogger("repolens.parsers.python")

_CONTAINER_KINDS = frozenset(
    {SymbolKind.CLASS, SymbolKind.FUNCTION, SymbolKind.ASYNC_FUNCTION}
)


def _is_async(node: Node) -> bool:
    return node.text.startswith(b"async")


class PythonParser(Parser):
    language = "python"
    parser_id = _version_of("tree-sitter-python", "tree-sitter-python")

    def __init__(self) -> None:
        self._ts_parser = TSParser(Language(_python_language()))

    # --- main entry ----------------------------------------------------------

    def parse(self, source: bytes, module: str = "") -> ParseOutcome:
        try:
            tree: Tree = self._ts_parser.parse(source)
        except Exception as exc:  # noqa: BLE001 - a hostile file must not crash the run
            logger.warning("tree-sitter python parse raised: %s", exc)
            return ParseOutcome(
                status=ParserStatus.FAILED,
                error_message=f"parser raised: {type(exc).__name__}",
            )

        has_error = tree.root_node.has_error
        symbols = self._extract_symbols(tree.root_node, source, module)
        symbols.extend(self._extract_import_symbols(tree.root_node))
        imports = self._extract_imports(tree.root_node, source)

        status = ParserStatus.SYNTAX_ERROR if has_error else ParserStatus.PARSED
        return ParseOutcome(
            status=status,
            symbols=symbols,
            imports=imports,
            error_message="syntax error" if has_error else None,
        )

    # --- symbols -------------------------------------------------------------

    def _extract_symbols(
        self, root: Node, source: bytes, module: str
    ) -> list[SymbolNode]:
        symbols: list[SymbolNode] = []
        ancestors: list[tuple[int, SymbolNode]] = []

        def visit(node: Node) -> None:
            target = self._unwrap(node)
            if target is None:
                return
            kind = self._symbol_kind(target)
            if kind is None:
                visit_children(node)
                return

            name = _name_of(target)
            if not name:
                visit_children(target)
                return

            start_line, start_column = _ptr_to_loc(target.start_point)
            end_line, end_column = _ptr_to_loc(target.end_point)

            parent_index = ancestors[-1][0] if ancestors else None

            chain = ".".join(a[1].name for a in ancestors)
            qualified = f"{chain}.{name}" if chain else name
            if module:
                qualified = f"{module}.{qualified}"

            parameters = target.child_by_field_name("parameters")
            signature = (
                f"{name}{_slice(source, parameters.start_byte, parameters.end_byte)}"
                if parameters is not None
                else None
            )

            symbol_index = len(symbols)
            symbol = SymbolNode(
                name=name,
                kind=kind,
                start_line=start_line,
                start_column=start_column,
                end_line=end_line,
                end_column=end_column,
                parent_index=parent_index,
                qualified_name=qualified,
                exported=False,
                signature=signature,
            )
            symbols.append(symbol)

            if kind in _CONTAINER_KINDS:
                ancestors.append((symbol_index, symbol))
                visit_children(target)
                ancestors.pop()
            else:
                visit_children(target)

        def visit_children(node: Node) -> None:
            for child in node.children:
                visit(child)

        visit_children(root)
        return symbols

    def _symbol_kind(self, node: Node) -> Optional[SymbolKind]:
        if node.type == "function_definition":
            return SymbolKind.ASYNC_FUNCTION if _is_async(node) else SymbolKind.FUNCTION
        if node.type == "class_definition":
            return SymbolKind.CLASS
        return None

    def _unwrap(self, node: Node) -> Optional[Node]:
        """Return the declaration behind ``decorated_definition`` wrappers."""
        if node.type == "decorated_definition":
            for child in node.children:
                if child.type in ("function_definition", "class_definition"):
                    return child
            return None
        return node

    # --- imports -------------------------------------------------------------

    def _extract_import_symbols(self, root: Node) -> list[SymbolNode]:
        """Register IMPORT-kind symbols so all languages expose imports uniformly."""
        symbols: list[SymbolNode] = []
        module = ""

        def note(node: Node, name: str) -> None:
            start_line, start_column = _ptr_to_loc(node.start_point)
            end_line, end_column = _ptr_to_loc(node.end_point)
            symbols.append(
                SymbolNode(
                    name=name,
                    kind=SymbolKind.IMPORT,
                    start_line=start_line,
                    start_column=start_column,
                    end_line=end_line,
                    end_column=end_column,
                    parent_index=None,
                    qualified_name=name,
                    exported=False,
                )
            )

        def visit(node: Node) -> None:
            if node.type == "import_statement":
                for child in node.children:
                    if child.type == "dotted_name":
                        note(child, _text(child).split(".")[0])
                    elif child.type == "aliased_import":
                        identifier = _first_child(child, "identifier")
                        if identifier is not None:
                            note(identifier, _text(identifier))
                return
            if node.type == "import_from_statement":
                in_names = False
                for child in node.children:
                    if child.type == "import":
                        in_names = True
                        continue
                    if not in_names:
                        continue
                    if child.type == "dotted_name":
                        note(child, _text(child))
                    elif child.type == "aliased_import":
                        identifier = _first_child(child, "identifier")
                        if identifier is not None:
                            note(identifier, _text(identifier))
                return
            for child in node.children:
                visit(child)

        visit(root)
        return symbols

    def _extract_imports(self, root: Node, source: bytes) -> list[ImportNode]:
        found: list[ImportNode] = []

        def visit(node: Node) -> None:
            if node.type == "import_statement":
                found.extend(self._import_statement(node))
                return
            if node.type == "import_from_statement":
                found.extend(self._import_from_statement(node))
            for child in node.children:
                visit(child)

        visit(root)
        return found

    def _import_statement(self, node: Node) -> list[ImportNode]:
        rows: list[ImportNode] = []
        start_line, _ = _ptr_to_loc(node.start_point)
        end_line, _ = _ptr_to_loc(node.end_point)
        for child in node.children:
            if child.type == "dotted_name":
                rows.append(
                    ImportNode(
                        source=child.text.decode("utf-8", errors="replace"),
                        imported_name=None,
                        alias=None,
                        kind=ImportKind.PYTHON_IMPORT,
                        start_line=start_line,
                        end_line=end_line,
                    )
                )
            elif child.type == "aliased_import":
                dotted = _first_child(child, "dotted_name")
                identifier = _first_child(child, "identifier")
                rows.append(
                    ImportNode(
                        source=_text(dotted),
                        imported_name=None,
                        alias=_text(identifier),
                        kind=ImportKind.PYTHON_IMPORT,
                        start_line=start_line,
                        end_line=end_line,
                    )
                )
        return rows

    def _import_from_statement(self, node: Node) -> list[ImportNode]:
        rows: list[ImportNode] = []
        start_line, _ = _ptr_to_loc(node.start_point)
        end_line, _ = _ptr_to_loc(node.end_point)

        module: Optional[str] = None
        for child in node.children:
            if child.type in ("dotted_name", "relative_import"):
                module = _text(child)
                break

        in_names = False
        for child in node.children:
            if child.type == "import":
                in_names = True
                continue
            if not in_names:
                continue
            if child.type == "wildcard_import":
                rows.append(
                    ImportNode(
                        source=module or "",
                        imported_name="*",
                        alias=None,
                        kind=ImportKind.PYTHON_FROM_IMPORT,
                        start_line=start_line,
                        end_line=end_line,
                    )
                )
            elif child.type == "dotted_name":
                rows.append(
                    ImportNode(
                        source=module or "",
                        imported_name=_text(child),
                        alias=None,
                        kind=ImportKind.PYTHON_FROM_IMPORT,
                        start_line=start_line,
                        end_line=end_line,
                    )
                )
            elif child.type == "aliased_import":
                dotted = _first_child(child, "dotted_name")
                identifier = _first_child(child, "identifier")
                rows.append(
                    ImportNode(
                        source=module or "",
                        imported_name=_text(dotted),
                        alias=_text(identifier),
                        kind=ImportKind.PYTHON_FROM_IMPORT,
                        start_line=start_line,
                        end_line=end_line,
                    )
                )
        return rows


# --- helpers -----------------------------------------------------------------


def _name_of(node: Node) -> Optional[str]:
    name_node = node.child_by_field_name("name")
    return _text(name_node) if name_node is not None else None


def _text(node: Optional[Node]) -> str:
    if node is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _first_child(node: Node, node_type: str) -> Optional[Node]:
    for child in node.children:
        if child.type == node_type:
            return child
    return None
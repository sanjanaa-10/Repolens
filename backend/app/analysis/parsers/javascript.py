"""Deterministic JavaScript syntax-structure extraction via tree-sitter.

Handles functions, generators, arrow functions, classes and methods,
interfaces/type aliases/enums when the TypeScript grammar is in use, ES module
imports/exports, and CommonJS ``require`` calls. JSX/TSX are parsed with the
TSX grammar (the JavaScript grammar has no JSX support), which is a superset
of plain JavaScript for the constructs we extract.
"""
from __future__ import annotations

import logging
from typing import Optional

from tree_sitter import Language, Node, Parser as TSParser, Tree

from app.analysis.kinds import ExportKind, ImportKind, ParserStatus, SymbolKind
from app.analysis.models import ExportNode, ImportNode, ParseOutcome, SymbolNode
from app.analysis.parsers.base import Parser, _ptr_to_loc, _slice, _version_of

logger = logging.getLogger("repolens.parsers.ecmascript")

# Node types that become symbols, plus whether they can contain children.
_CONTAINER_TYPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "method_definition",
        "arrow_function",
    }
)


class EcmaParser(Parser):
    """Parser for the JavaScript/TypeScript family.

    ``grammar`` selects the concrete syntax tree: ``javascript`` (JS),
    ``typescript`` (TS), or ``tsx`` (JSX/TSX). Symbol extraction is shared.
    """

    language = "javascript"
    parser_id = _version_of("tree-sitter-javascript", "tree-sitter-javascript")

    _GRAMMARS = {
        "javascript": None,
        "typescript": None,
        "tsx": None,
    }

    def __init__(self, grammar: str = "javascript") -> None:
        from tree_sitter_javascript import language as js_language
        from tree_sitter_typescript import language_tsx as tsx_language
        from tree_sitter_typescript import language_typescript as ts_language

        self._GRAMMARS["javascript"] = js_language
        self._GRAMMARS["typescript"] = ts_language
        self._GRAMMARS["tsx"] = tsx_language

        self.language = grammar
        if grammar == "typescript":
            self.parser_id = _version_of(
                "tree-sitter-typescript", "tree-sitter-typescript"
            )
        elif grammar == "tsx":
            self.parser_id = f"{_version_of('tree-sitter-typescript', 'tree-sitter-typescript')} (tsx)"
        else:
            self.parser_id = _version_of(
                "tree-sitter-javascript", "tree-sitter-javascript"
            )
        self._ts_parser = TSParser(Language(self._GRAMMARS[grammar]()))

    # --- main entry ----------------------------------------------------------

    def parse(self, source: bytes, module: str = "") -> ParseOutcome:
        try:
            tree: Tree = self._ts_parser.parse(source)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tree-sitter %s parse raised: %s", self.language, exc)
            return ParseOutcome(
                status=ParserStatus.FAILED,
                error_message=f"parser raised: {type(exc).__name__}",
            )

        has_error = tree.root_node.has_error
        symbols = self._extract_symbols(tree.root_node, source, module)
        imports = self._extract_imports(tree.root_node, source)
        exports = self._extract_exports(tree.root_node, source)

        status = ParserStatus.SYNTAX_ERROR if has_error else ParserStatus.PARSED
        return ParseOutcome(
            status=status,
            symbols=symbols,
            imports=imports,
            exports=exports,
            error_message="syntax error" if has_error else None,
        )

    # --- symbols -------------------------------------------------------------

    def _extract_symbols(
        self, root: Node, source: bytes, module: str
    ) -> list[SymbolNode]:
        symbols: list[SymbolNode] = []
        # stack of (symbol_index, SymbolNode) that can contain nested symbols
        ancestors: list[tuple[int, SymbolNode]] = []

        def push(
            exported: bool, node: Node, name: str, kind: SymbolKind
        ) -> None:
            start_line, start_column = _ptr_to_loc(node.start_point)
            end_line, end_column = _ptr_to_loc(node.end_point)
            parent_index = ancestors[-1][0] if ancestors else None

            chain = ".".join(a[1].name for a in ancestors)
            qualified = f"{chain}.{name}" if chain else name
            if module:
                qualified = f"{module}.{qualified}"

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
                exported=exported,
                signature=signature_for(node, name, source),
            )
            symbols.append(symbol)

            if kind in (
                SymbolKind.FUNCTION,
                SymbolKind.ASYNC_FUNCTION,
                SymbolKind.CLASS,
                SymbolKind.METHOD,
                SymbolKind.ARROW_FUNCTION,
            ):
                ancestors.append((symbol_index, symbol))
                visit_children(node, False)
                ancestors.pop()
            else:
                visit_children(node, False)

        def visit(node: Node, exported: bool = False) -> None:
            node_type = node.type

            if node_type == "export_statement":
                visit_children(node, exported=True)
                return

            if node_type == "import_statement":
                self._note_import_symbols(node, symbols)
                return

            if node_type == "lexical_declaration" or node_type == "variable_statement":
                for child in node.children:
                    if child.type == "variable_declarator":
                        _visit_declarator(child, exported)
                return

            if node_type == "class_declaration":
                name = text_of(node.child_by_field_name("name"))
                if name:
                    push(exported, node, name, SymbolKind.CLASS)
                    return
                visit_children(node, exported)
                return

            if node_type == "function_declaration":
                name = text_of(node.child_by_field_name("name"))
                if name:
                    kind = (
                        SymbolKind.ASYNC_FUNCTION
                        if node.text.startswith(b"async")
                        else SymbolKind.FUNCTION
                    )
                    push(exported, node, name, kind)
                    return
                visit_children(node, exported)
                return

            if node_type == "generator_function_declaration":
                name = text_of(node.child_by_field_name("name"))
                if name:
                    push(exported, node, name, SymbolKind.FUNCTION)
                    return
                visit_children(node, exported)
                return

            if node_type == "method_definition":
                name = text_of(_field(node, "name", "property"))
                if name:
                    push(exported, node, name, SymbolKind.METHOD)
                    return
                visit_children(node, exported)
                return

            if node_type == "field_definition":
                value = node.child_by_field_name("value")
                if value is not None and value.type == "arrow_function":
                    name = text_of(_field(node, "name", "property"))
                    if name:
                        push(exported, value, name, SymbolKind.ARROW_FUNCTION)
                        return
                visit_children(node, exported)
                return

            if node_type in (
                "interface_declaration",
                "type_alias_declaration",
                "enum_declaration",
            ):
                name = text_of(node.child_by_field_name("name"))
                if name:
                    kind = {
                        "interface_declaration": SymbolKind.INTERFACE,
                        "type_alias_declaration": SymbolKind.TYPE_ALIAS,
                        "enum_declaration": SymbolKind.ENUM,
                    }[node_type]
                    push(exported, node, name, kind)
                    return
                visit_children(node, exported)
                return

            visit_children(node, exported)

        def visit_children(node: Node, exported: bool) -> None:
            for child in node.children:
                visit(child, exported)

        def _visit_declarator(decl: Node, exported: bool) -> None:
            name = text_of(decl.child_by_field_name("name"))
            value = decl.child_by_field_name("value")
            if value is not None and value.type == "arrow_function":
                if name:
                    push(exported, value, name, SymbolKind.ARROW_FUNCTION)
                    return
            elif exported and name:
                push(exported, decl, name, SymbolKind.VARIABLE)
                return
            visit(decl, exported)

        visit(root)
        return symbols

    def _note_import_symbols(
        self, node: Node, symbols: list[SymbolNode]
    ) -> None:
        for name, name_node in self._bound_import_names(node):
            if not name:
                continue
            start_line, start_column = _ptr_to_loc(name_node.start_point)
            end_line, end_column = _ptr_to_loc(name_node.end_point)
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

    def _bound_import_names(self, node: Node) -> list[tuple[str, Node]]:
        """Return (bound name, node) pairs introduced by an import statement."""
        result: list[tuple[str, Node]] = []
        clause = _find_import_clause(node)
        if clause is None:
            return result
        for child in clause.children:
            if child.type == "identifier":
                result.append((text_of(child), child))
            elif child.type == "namespace_import":
                ident = last_child_of_type(child, "identifier")
                if ident is not None:
                    result.append((text_of(ident), ident))
            elif child.type == "named_imports":
                for spec in child.children:
                    if spec.type != "import_specifier":
                        continue
                    alias = spec.child_by_field_name("alias")
                    name = spec.child_by_field_name("name")
                    bind = alias if alias is not None else name
                    if bind is not None:
                        result.append((text_of(bind), bind))
        return result

    # --- imports -------------------------------------------------------------

    def _extract_imports(self, root: Node, source: bytes) -> list[ImportNode]:
        rows: list[ImportNode] = []
        seen_ranges: set[tuple[int, int]] = set()

        def visit(node: Node) -> None:
            if node.type == "import_statement":
                src = _strip_quotes(text_of(node.child_by_field_name("source")))
                start_line, _ = _ptr_to_loc(node.start_point)
                end_line, _ = _ptr_to_loc(node.end_point)
                clause = _find_import_clause(node)
                if clause is None:
                    rows.append(
                        ImportNode(
                            source=src,
                            imported_name=None,
                            alias=None,
                            kind=ImportKind.ES_MODULE,
                            start_line=start_line,
                            end_line=end_line,
                        )
                    )
                    return
                for child in clause.children:
                    if child.type == "identifier":
                        rows.append(
                            ImportNode(
                                source=src,
                                imported_name=None,
                                alias=_strip_quotes(text_of(child)),
                                kind=ImportKind.ES_MODULE,
                                start_line=start_line,
                                end_line=end_line,
                            )
                        )
                    elif child.type == "namespace_import":
                        ident = last_child_of_type(child, "identifier")
                        rows.append(
                            ImportNode(
                                source=src,
                                imported_name=None,
                                alias=text_of(ident),
                                kind=ImportKind.ES_MODULE,
                                start_line=start_line,
                                end_line=end_line,
                            )
                        )
                    elif child.type == "named_imports":
                        for spec in child.children:
                            if spec.type != "import_specifier":
                                continue
                            name = text_of(spec.child_by_field_name("name"))
                            alias = text_of(spec.child_by_field_name("alias"))
                            rows.append(
                                ImportNode(
                                    source=src,
                                    imported_name=name or None,
                                    alias=alias or None,
                                    kind=ImportKind.ES_MODULE,
                                    start_line=start_line,
                                    end_line=end_line,
                                )
                            )
                return
            if node.type == "variable_declarator":
                key = (node.start_byte, node.end_byte)
                if key in seen_ranges:
                    visit_children(node)
                    return
                req = _find_require(node, source)
                if req is not None:
                    seen_ranges.add(key)
                    name = text_of(node.child_by_field_name("name"))
                    start_line, _ = _ptr_to_loc(node.start_point)
                    end_line, _ = _ptr_to_loc(node.end_point)
                    rows.append(
                        ImportNode(
                            source=req,
                            imported_name=name or None,
                            alias=None,
                            kind=ImportKind.COMMONJS,
                            start_line=start_line,
                            end_line=end_line,
                        )
                    )
                return
            visit_children(node)

        def visit_children(node: Node) -> None:
            for child in node.children:
                visit(child)

        visit(root)
        return rows

    # --- exports -------------------------------------------------------------

    def _extract_exports(self, root: Node, source: bytes) -> list[ExportNode]:
        rows: list[ExportNode] = []

        def visit(node: Node) -> None:
            if node.type != "export_statement":
                for child in node.children:
                    visit(child)
                return

            start_line, _ = _ptr_to_loc(node.start_point)
            end_line, _ = _ptr_to_loc(node.end_point)

            is_default = any(c.type == "default" for c in node.children)
            matched = False
            for child in node.children:
                if child.type in (
                    "function_declaration",
                    "generator_function_declaration",
                    "class_declaration",
                    "interface_declaration",
                    "type_alias_declaration",
                    "enum_declaration",
                ):
                    name = text_of(child.child_by_field_name("name"))
                    rows.append(
                        ExportNode(
                            name=name or "default",
                            kind=ExportKind.DEFAULT if is_default else ExportKind.NAMED,
                            start_line=start_line,
                            end_line=end_line,
                        )
                    )
                    matched = True
                    continue
                if child.type == "lexical_declaration":
                    for decl in child.children:
                        if decl.type == "variable_declarator":
                            name = text_of(decl.child_by_field_name("name"))
                            if name:
                                rows.append(
                                    ExportNode(
                                        name=name,
                                        kind=(
                                            ExportKind.DEFAULT
                                            if is_default
                                            else ExportKind.NAMED
                                        ),
                                        start_line=start_line,
                                        end_line=end_line,
                                    )
                                )
                                matched = True
                    continue
                if child.type == "export_clause":
                    for spec in child.children:
                        if spec.type != "export_specifier":
                            continue
                        alias = text_of(spec.child_by_field_name("alias"))
                        name = text_of(spec.child_by_field_name("name"))
                        rows.append(
                            ExportNode(
                                name=alias or name or "?",
                                kind=ExportKind.NAMED,
                                start_line=start_line,
                                end_line=end_line,
                            )
                        )
                    matched = True
                    continue
                if child.type == "*":
                    rows.append(
                        ExportNode(
                            name="*",
                            kind=ExportKind.STAR,
                            start_line=start_line,
                            end_line=end_line,
                        )
                    )
                    matched = True
                    continue

            if is_default and not matched:
                rows.append(
                    ExportNode(
                        name="default",
                        kind=ExportKind.DEFAULT,
                        start_line=start_line,
                        end_line=end_line,
                    )
                )

        visit(root)
        return rows


def signature_for(node: Node, name: str, source: bytes) -> Optional[str]:
    params = node.child_by_field_name("parameters")
    if params is not None:
        return f"{name}{_slice(source, params.start_byte, params.end_byte)}"
    if node.type == "arrow_function":
        body = node.child_by_field_name("body")
        if body is not None:
            head = _slice(source, node.start_byte, body.start_byte).strip()
            return f"{name} = {head}"
    return name


def text_of(node: Optional[Node]) -> str:
    if node is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _field(node: Node, *names: str) -> Optional[Node]:
    for name in names:
        found = node.child_by_field_name(name)
        if found is not None:
            return found
    return None


def last_child_of_type(node: Node, node_type: str) -> Optional[Node]:
    found: Optional[Node] = None
    for child in node.children:
        if child.type == node_type:
            found = child
    return found


def _find_import_clause(node: Node) -> Optional[Node]:
    """Locate the ``import_clause`` child of an import statement.

    The clause is a named (anonymous-position) child, not a field, in the
    JavaScript grammar, so scanning typed children is the portable approach.
    """
    for child in node.children:
        if child.type == "import_clause":
            return child
    return None


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"', "`"):
        return value[1:-1]
    return value


def _find_require(node: Node, source: bytes) -> Optional[str]:
    """Return the module path if ``node`` contains a require('...') call."""
    def scan(current: Node, depth: int) -> Optional[str]:
        if depth > 12 or current.type == "function":
            return None
        if current.type == "call_expression":
            fn = current.child_by_field_name("function")
            args = current.child_by_field_name("arguments")
            if fn is not None and _strip_quotes(text_of(fn)) == "require" and args is not None:
                for arg in args.named_children:
                    if arg.type == "string":
                        return _strip_quotes(text_of(arg))
                    break
                return None
        if current.type == "string":
            return None
        for child in current.children:
            hit = scan(child, depth + 1)
            if hit is not None:
                return hit
        return None

    return scan(node, 0)
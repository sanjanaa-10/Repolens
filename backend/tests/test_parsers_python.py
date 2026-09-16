"""Unit tests for the deterministic Python parser."""
from __future__ import annotations

import pytest

from app.analysis.kinds import ParserStatus, SymbolKind
from app.analysis.parsers.registry import get_parser

SOURCE = """\
import os
from typing import Optional

async def fetch(url: str) -> dict:
    return {}

def handler(arg):
    return arg

class Service:
    def __init__(self, client):
        self.client = client

    async def start(self):
        pass

@dec
def wrapped():
    pass

def outer():
    def inner():
        return 1
    return inner
"""


@pytest.fixture(scope="module")
def python_outcome():
    return get_parser("python").parse(SOURCE.encode(), module="pkg.mod")


def test_status_parsed(python_outcome) -> None:
    assert python_outcome.status is ParserStatus.PARSED
    assert python_outcome.error_message is None


def test_symbols_and_qualified_names(python_outcome) -> None:
    names = [s.qualified_name for s in python_outcome.symbols]
    assert "pkg.mod.fetch" in names
    assert "pkg.mod.handler" in names
    assert "pkg.mod.Service" in names
    assert "pkg.mod.Service.__init__" in names
    assert "pkg.mod.Service.start" in names
    assert "pkg.mod.wrapped" in names
    assert "pkg.mod.outer" in names
    assert "pkg.mod.outer.inner" in names


def test_kinds(python_outcome) -> None:
    by_name = {s.name: s.kind for s in python_outcome.symbols}
    assert by_name["fetch"] is SymbolKind.ASYNC_FUNCTION
    assert by_name["handler"] is SymbolKind.FUNCTION
    assert by_name["Service"] is SymbolKind.CLASS
    assert by_name["__init__"] is SymbolKind.FUNCTION
    assert by_name["start"] is SymbolKind.ASYNC_FUNCTION
    assert by_name["wrapped"] is SymbolKind.FUNCTION


def test_exact_locations(python_outcome) -> None:
    fetch = next(s for s in python_outcome.symbols if s.name == "fetch")
    assert (fetch.start_line, fetch.start_column) == (4, 1)
    assert (fetch.end_line, fetch.end_column) == (5, 14)

    service = next(s for s in python_outcome.symbols if s.name == "Service")
    assert (service.start_line, service.start_column) == (10, 1)
    assert (service.end_line, service.end_column) == (15, 13)

    init = next(s for s in python_outcome.symbols if s.name == "__init__")
    assert (init.start_line, init.start_column) == (11, 5)
    assert (init.end_line, init.end_column) == (12, 29)

    inner = next(s for s in python_outcome.symbols if s.name == "inner")
    assert (inner.start_line, inner.start_column) == (22, 5)
    assert inner.parent_index == python_outcome.symbols.index(
        next(s for s in python_outcome.symbols if s.name == "outer")
    )


def test_signatures(python_outcome) -> None:
    by_name = {s.qualified_name: s for s in python_outcome.symbols}
    assert by_name["pkg.mod.fetch"].signature == "fetch(url: str)"
    assert by_name["pkg.mod.handler"].signature == "handler(arg)"
    assert by_name["pkg.mod.Service.__init__"].signature == "__init__(self, client)"
    assert by_name["pkg.mod.outer.inner"].signature == "inner()"
    assert by_name["pkg.mod.Service"].signature is None


def test_imports_table(python_outcome) -> None:
    rows = python_outcome.imports
    kinds = {(r.source, r.imported_name, r.alias) for r in rows}
    assert ("os", None, None) in kinds
    assert ("typing", "Optional", None) in kinds
    assert rows[0].kind == "python_import"
    assert rows[1].kind == "python_from_import"
    assert all(r.start_line >= 1 and r.end_line >= r.start_line for r in rows)


def test_import_symbols(python_outcome) -> None:
    imports = {s.name for s in python_outcome.symbols if s.kind is SymbolKind.IMPORT}
    assert imports == {"os", "Optional"}


def test_module_level_positions_are_one_based(python_outcome) -> None:
    first = python_outcome.symbols[0]
    assert first.start_line >= 1
    assert first.start_column >= 1


def test_syntax_error_status() -> None:
    outcome = get_parser("python").parse(b"def broken(:\n    pass\n")
    assert outcome.status is ParserStatus.SYNTAX_ERROR
    assert outcome.error_message == "syntax error"
    # Lenient parsing still yields whatever is structurally recognizable.
    assert any(s.name == "broken" for s in outcome.symbols)


def test_unsupported_encoding_does_not_raise() -> None:
    source = "def \u00ff():\n    pass\n".encode("latin-1", errors="ignore") + b"\xff\xfe"
    outcome = get_parser("python").parse(source)
    assert outcome.status in (ParserStatus.PARSED, ParserStatus.SYNTAX_ERROR)


def test_from_import_with_alias_and_wildcard() -> None:
    source = b"from . import util\nfrom ..core import run_task as rt\nfrom m import *\n"
    outcome = get_parser("python").parse(source)
    kinds = {(r.imported_name, r.alias) for r in outcome.imports}
    assert ("util", None) in kinds
    assert ("run_task", "rt") in kinds
    assert ("*", None) in kinds
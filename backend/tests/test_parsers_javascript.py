"""Unit tests for the deterministic JavaScript/TypeScript parser."""
from __future__ import annotations

import pytest

from app.analysis.kinds import ExportKind, ImportKind, ParserStatus, SymbolKind
from app.analysis.parsers.registry import get_parser

JS = """\
import React, { useState as useSt } from "react";
import * as d3 from "d3";

export const HOST = process.env.HOST;
export default function App(props) {
  return <div />;
}
const util = require("./util.js");
export function helper(x, y) { return x + y; }
export async function fetchIt(url, opts = {}) {}
export class Widget {
  constructor(name) { this.name = name; }
  draw(ctx) {}
  onClick = (e) => {};
}
const double = (n) => n * 2;
export { helper as h };
export { double };
export * from "./extra.js";
export default () => 1;
function hidden() {}
"""


@pytest.fixture(scope="module")
def js_outcome():
    return get_parser("javascript").parse(JS.encode())


def test_js_status(js_outcome) -> None:
    assert js_outcome.status is ParserStatus.PARSED


def test_js_symbols_kinds(js_outcome) -> None:
    by_name = {s.name: s.kind for s in js_outcome.symbols}
    assert by_name["App"] is SymbolKind.FUNCTION
    assert by_name["helper"] is SymbolKind.FUNCTION
    assert by_name["fetchIt"] is SymbolKind.ASYNC_FUNCTION
    assert by_name["Widget"] is SymbolKind.CLASS
    assert by_name["constructor"] is SymbolKind.METHOD
    assert by_name["draw"] is SymbolKind.METHOD
    assert by_name["onClick"] is SymbolKind.ARROW_FUNCTION
    assert by_name["double"] is SymbolKind.ARROW_FUNCTION
    assert by_name["hidden"] is SymbolKind.FUNCTION
    assert by_name["HOST"] is SymbolKind.VARIABLE
    assert by_name["React"] is SymbolKind.IMPORT
    assert by_name["useSt"] is SymbolKind.IMPORT
    assert by_name["d3"] is SymbolKind.IMPORT


def test_js_exported_flags(js_outcome) -> None:
    exported = {s.name for s in js_outcome.symbols if s.exported}
    assert {"HOST", "App", "helper", "fetchIt", "Widget"} <= exported
    assert "hidden" not in exported
    assert "double" not in exported


def test_js_parent_linking(js_outcome) -> None:
    widget = next(s for s in js_outcome.symbols if s.name == "Widget")
    onclick = next(s for s in js_outcome.symbols if s.name == "onClick")
    assert onclick.parent_index == js_outcome.symbols.index(widget)


def test_js_imports(js_outcome) -> None:
    rows = js_outcome.imports
    assert (rows[0].source, rows[0].alias) == ("react", "React")
    assert (rows[0].imported_name, rows[0].alias, rows[0].kind) == (
        None,
        "React",
        ImportKind.ES_MODULE.value,
    )
    assert (rows[1].source, rows[1].imported_name, rows[1].alias) == (
        "react",
        "useState",
        "useSt",
    )
    assert (rows[2].source, rows[2].kind) == ("d3", ImportKind.ES_MODULE.value)
    commonjs = [r for r in rows if r.kind == ImportKind.COMMONJS.value]
    assert commonjs[0].source == "./util.js"
    assert commonjs[0].imported_name == "util"


def test_js_exports(js_outcome) -> None:
    rows = {(r.name, r.kind) for r in js_outcome.exports}
    assert ("HOST", ExportKind.NAMED.value) in rows
    assert ("App", ExportKind.DEFAULT.value) in rows
    assert ("helper", ExportKind.NAMED.value) in rows
    assert ("fetchIt", ExportKind.NAMED.value) in rows
    assert ("Widget", ExportKind.NAMED.value) in rows
    assert ("h", ExportKind.NAMED.value) in rows  # alias
    assert ("double", ExportKind.NAMED.value) in rows
    assert ("*", ExportKind.STAR.value) in rows
    assert ("default", ExportKind.DEFAULT.value) in rows  # export default () => 1


def test_js_signatures(js_outcome) -> None:
    helper = next(s for s in js_outcome.symbols if s.name == "helper")
    assert helper.signature == "helper(x, y)"
    double = next(s for s in js_outcome.symbols if s.name == "double")
    assert double.signature == "double(n)"


TS = """\
import { Router } from "express";

export interface Item {
  id: string;
}

type Callback = (err: Error | null) => void;

export enum State {
  IDLE = "idle",
}

export const createRouter = (): Router => {
  return Router();
};

export default function configure(): Router {
  return createRouter();
}

class Impl implements Item {
  id = "";
  label = "";
}
"""


@pytest.fixture(scope="module")
def ts_outcome():
    return get_parser("typescript").parse(TS.encode(), module="routing")


def test_ts_kinds(ts_outcome) -> None:
    by_name = {s.name: s.kind for s in ts_outcome.symbols}
    assert by_name["Item"] is SymbolKind.INTERFACE
    assert by_name["Callback"] is SymbolKind.TYPE_ALIAS
    assert by_name["State"] is SymbolKind.ENUM
    assert by_name["createRouter"] is SymbolKind.ARROW_FUNCTION
    assert by_name["configure"] is SymbolKind.FUNCTION
    assert by_name["Impl"] is SymbolKind.CLASS
    assert "routing.Item" in {s.qualified_name for s in ts_outcome.symbols}


def test_ts_exports(ts_outcome) -> None:
    rows = {(r.name, r.kind) for r in ts_outcome.exports}
    assert ("Item", ExportKind.NAMED.value) in rows
    assert ("State", ExportKind.NAMED.value) in rows
    assert ("createRouter", ExportKind.NAMED.value) in rows
    assert ("configure", ExportKind.DEFAULT.value) in rows


TSX = """\
import type { FC } from "react";

export interface Props {
  title: string;
}

const Header: FC<Props> = ({ title }) => <h1>{title}</h1>;

export default Header;
"""


def test_tsx_parses_with_jsx_and_default_identifier_export() -> None:
    outcome = get_parser("tsx").parse(TSX.encode(), module="ui")
    by_name = {s.name: s.kind for s in outcome.symbols}
    assert by_name["Props"] is SymbolKind.INTERFACE
    assert by_name["Header"] is SymbolKind.ARROW_FUNCTION
    assert "ui.Header" in {s.qualified_name for s in outcome.symbols}
    assert ("default", ExportKind.DEFAULT.value) in {
        (r.name, r.kind) for r in outcome.exports
    }


def test_jsx_uses_tsx_fallback_grammar() -> None:
    source = b"export const view = () => <h1>hi</h1>;\n"
    outcome = get_parser("jsx").parse(source, module="views")
    assert outcome.status is ParserStatus.PARSED
    assert any(s.kind is SymbolKind.ARROW_FUNCTION for s in outcome.symbols)


def test_js_syntax_error_still_yields_symbols() -> None:
    outcome = get_parser("javascript").parse(b"function broken( {}\n")
    assert outcome.status is ParserStatus.SYNTAX_ERROR
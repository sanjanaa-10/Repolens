import sys

from app.analysis.parsers.registry import get_parser, parser_versions

PY = b'''import os
import os.path as osp
from typing import Optional, List as L
from . import util
from ..core import run_task

async def fetch(url: str) -> dict:
    return {}

def handler(arg, /, *, flag=True):
    return fetch(arg)

class Service:
    BASE = 1

    def __init__(self, client):
        self.client = client

    async def start(self):
        pass

    @util.marker
    def decorated(self):
        pass

class Child(Service):
    pass

def top_level():
    def inner():
        pass
    return inner

x = 1
'''

JS = b'''import React, { useState as useSt } from "react";
import * as d3 from "d3";

export const HOST = process.env.HOST;
export default function App(props) {
  const [state, setState] = useState(0);
  return null;
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
'''

TS = b'''import { Router } from "express";

export interface Item {
  id: string;
  label: string;
}

type Callback = (err: Error | null, data: Item) => void;

export enum State {
  IDLE = "idle",
  DONE = "done",
}

export const createRouter = (): Router => {
  return Router();
};

class Impl implements Item {
  id = "";
  label = "";
}

export default function configure(): Router {
  return createRouter();
}
'''

TSX = b'''import type { FC } from "react";

export interface Props {
  title: string;
}

const Header: FC<Props> = ({ title }) => <h1>{title}</h1>;

export default Header;
'''

BAD_PY = b"def broken(:\n    pass\n"


def dump(tag: str, source: bytes, language: str) -> None:
    parser = get_parser(language)
    module = "demo"
    outcome = parser.parse(source, module=module)
    print(f"=== {tag} [{language}] status={outcome.status} err={outcome.error_message}")
    for s in outcome.symbols:
        print(
            f"  sym {s.kind.value:15s} {s.qualified_name:30s} "
            f"L{s.start_line}:{s.start_column}-{s.end_line}:{s.end_column} "
            f"parent={s.parent_index} exported={s.exported} sig={s.signature!r}"
        )
    for i in outcome.imports:
        print(
            f"  imp {i.kind.value:15s} source={i.source!r} name={i.imported_name!r} "
            f"alias={i.alias!r} L{i.start_line}-{i.end_line}"
        )
    for e in outcome.exports:
        print(f"  exp {e.kind.value:8s} name={e.name!r} L{e.start_line}-{e.end_line}")


dump("python", PY, "python")
dump("javascript", JS, "javascript")
dump("typescript", TS, "typescript")
dump("tsx", TSX, "tsx")
dump("bad_python", BAD_PY, "python")
print("versions:", parser_versions())
print("OK")
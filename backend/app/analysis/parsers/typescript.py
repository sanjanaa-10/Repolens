"""TypeScript/TSX parse-ticket classes.

The TypeScript and TSX grammars are handled by :class:`EcmaParser`; these
subclasses pin the concrete grammar so the registry can look them up by
language without knowing grammar details.
"""
from __future__ import annotations

from app.analysis.parsers.javascript import EcmaParser


class TypeScriptParser(EcmaParser):
    """TypeScript grammar (no JSX)."""

    def __init__(self) -> None:
        super().__init__(grammar="typescript")


class TsxParser(EcmaParser):
    """TSX grammar (TypeScript + JSX), also used as the fallback for JSX."""

    def __init__(self) -> None:
        super().__init__(grammar="tsx")
"""Parser registry: maps normalized language identifiers to parser instances.

Instances are created lazily and cached; parser construction is cheap and
thread-safe enough for single-worker usage. ``jsx`` files are parsed with the
TSX grammar, which is a strict superset of JSX for the constructs we extract.
"""
from __future__ import annotations

from typing import Final

from app.analysis.kinds import SUPPORTED_PARSE_LANGUAGES
from app.analysis.parsers.base import Parser
from app.analysis.parsers.javascript import EcmaParser
from app.analysis.parsers.python import PythonParser
from app.analysis.parsers.typescript import TsxParser, TypeScriptParser

_js_parser: Final[EcmaParser] = EcmaParser("javascript")
_ts_parser: Final[TypeScriptParser] = TypeScriptParser()
_tsx_parser: Final[TsxParser] = TsxParser()
_py_parser: Final[PythonParser] = PythonParser()

_REGISTRY: Final[dict[str, Parser]] = {
    "python": _py_parser,
    "javascript": _js_parser,
    "jsx": _tsx_parser,
    "typescript": _ts_parser,
    "tsx": _tsx_parser,
}


def get_parser(language: str) -> Parser:
    """Return the singleton parser for a normalized language identifier.

    Raises ``KeyError`` for languages outside :data:`SUPPORTED_PARSE_LANGUAGES`.
    """
    if language not in _REGISTRY:
        raise KeyError(f"unsupported parse language: {language}")
    return _REGISTRY[language]


def is_parseable(language: str) -> bool:
    return language in SUPPORTED_PARSE_LANGUAGES


# Canonical names produced by the ingestion layer's language detection
# (app.repositories.languages), mapped to the parser grammar identifiers.
_DETECTED_TO_PARSE = {
    "Python": "python",
    "JavaScript": "javascript",
    "TypeScript": "typescript",
}


def parse_language_for(language: str | None, file_path: str) -> str | None:
    """Map a detected pretty-name language + path to a parse grammar id.

    JSX/TSX require the file extension to pick the grammar; everything else
    maps 1:1. Returns ``None`` when the file cannot be parsed.
    """
    if language is None:
        return None
    normalized = _DETECTED_TO_PARSE.get(language, language)
    if normalized not in SUPPORTED_PARSE_LANGUAGES:
        return None
    if normalized in ("javascript", "jsx") and file_path.lower().endswith(".jsx"):
        return "jsx"
    if normalized in ("typescript", "tsx") and file_path.lower().endswith(".tsx"):
        return "tsx"
    return normalized


def parser_versions() -> dict[str, str]:
    """language → grammar provenance (e.g. ``"tree-sitter-python 0.25.0"``)."""
    return {language: parser.version() for language, parser in _REGISTRY.items()}
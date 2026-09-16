"""Deterministic kinds used across the analysis pipeline.

These enums are the normalized vocabulary shared by parsers, the database,
and the API. Values are stable strings so they can be compared and persisted
directly.
"""
from __future__ import annotations

import enum


class SymbolKind(str, enum.Enum):
    """Container for symbol categories emitted by the parsers."""

    FUNCTION = "FUNCTION"
    ASYNC_FUNCTION = "ASYNC_FUNCTION"
    CLASS = "CLASS"
    METHOD = "METHOD"
    ARROW_FUNCTION = "ARROW_FUNCTION"
    INTERFACE = "INTERFACE"
    TYPE_ALIAS = "TYPE_ALIAS"
    ENUM = "ENUM"
    IMPORT = "IMPORT"
    VARIABLE = "VARIABLE"


class ParserStatus(str, enum.Enum):
    """Per-file parse outcome recorded in ``parse_results``."""

    PARSED = "parsed"
    SYNTAX_ERROR = "syntax_error"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class ImportKind(str, enum.Enum):
    """How an import statement was expressed in the source."""

    PYTHON_IMPORT = "python_import"
    PYTHON_FROM_IMPORT = "python_from_import"
    ES_MODULE = "es_module"
    COMMONJS = "commonjs"


class ExportKind(str, enum.Enum):
    """JavaScript/TypeScript export forms."""

    NAMED = "named"
    DEFAULT = "default"
    STAR = "star"


# Languages the parsing pipeline can handle. Keys match the normalised
# language identifiers stored on file records by the ingestion layer.
SUPPORTED_PARSE_LANGUAGES = frozenset(
    {"python", "javascript", "jsx", "typescript", "tsx"}
)
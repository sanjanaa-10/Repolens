"""Parser abstraction for deterministic source-structure extraction.

Every parser treats source strictly as text: it parses bytes with a concrete
syntax tree library, extracts symbols/imports/exports, and never imports,
evaluates, or executes repository code.
"""
from __future__ import annotations

import importlib.metadata
from abc import ABC, abstractmethod

from app.analysis.models import ParseOutcome


class Parser(ABC):
    """Base class for a language parser.

    Implementations map a concrete syntax tree into ``ParseOutcome`` using
    normalized kinds and 1-based line / column coordinates.
    """

    # Stable identifier used in parse_results (e.g. "python", "javascript").
    language: str = ""

    # Grammar library + version for provenance, e.g. "tree-sitter-python 0.25.0".
    parser_id: str = ""

    @abstractmethod
    def parse(self, source: bytes, module: str = "") -> ParseOutcome:
        """Parse raw source bytes and return the normalized outcome.

        ``module`` is the repository-relative dotted module path of the file
        (extension and ``__init__`` dropped); it is prefixed onto qualified
        symbol names by the caller when known, giving stable unique names.
        """
        raise NotImplementedError

    def version(self) -> str:
        return self.parser_id


def _ptr_to_loc(point) -> tuple[int, int]:
    """Convert a tree-sitter Point into 1-based (line, column)."""
    return point.row + 1, point.column + 1


def _slice(source: bytes, start_byte: int, end_byte: int) -> str:
    """Safely decode a byte range of the source for signatures/names."""
    return source[start_byte:end_byte].decode("utf-8", errors="replace")


def _version_of(distribution: str, fallback: str) -> str:
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return f"{distribution} {version}"
"""Deterministic search result ranking.

Ranking is pure: it never uses an LLM or scores by model probabilities. The
algorithm is a stable, reproducible ordering derived from string/coordinate
properties only, so repeated searches return identical results:

1. exact symbol (or qualified) name match
2. prefix match
3. substring (case-insensitive) match
4. filename/module relevance (query appears in the file path)
5. file path order within a tie for full determinism
"""
from __future__ import annotations

from dataclasses import dataclass

_TIEBREAK_QUERY = "_repolens_tiebreak_"


@dataclass(frozen=True)
class Rankable:
    key: object
    name: str
    qualified_name: str | None
    path: str
    kind: str = ""

    def _match_score(self, query: str, lower_query: str) -> tuple:
        """Return a (rank, stanza) used by ``finalize_rank``.

        Higher rank tuple wins; stanza disambiguates ties.",
        """
        if self.qualified_name:
            has_real_fq = self.qualified_name != self.name
            if self.qualified_name == query and has_real_fq:
                return (6, "")
            if self.qualified_name == self.name + "." + query and has_real_fq:
                return (5, "")

        if self.name == query:
            return (5, "")
        if self.qualified_name and self.qualified_name.endswith(query):
            return (4, "")
        if self.name.startswith(query):
            return (3, "")
        if self.name.lower() == lower_query:
            return (3, "ci-exact")
        if self.name.casefold().startswith(lower_query):
            return (2, "")

        path_lower = self.path.replace("\\", "/").lower()
        if query.lower() in path_lower and query.lower() != lower_query:
            return (2, "path")

        if lower_query in self.name.casefold():
            return (1, "")
        return (0, "")


# Definition kinds outrank import/export/alias stubs for the same name match.
_KIND_PREF: dict[str, int] = {
    "FUNCTION": 10,
    "ASYNC_FUNCTION": 10,
    "METHOD": 10,
    "CLASS": 9,
    "INTERFACE": 9,
    "ENUM": 8,
    "TYPE_ALIAS": 8,
    "VARIABLE": 7,
    "CONSTANT": 7,
    "IMPORT": 0,
    "EXPORT": 0,
    "TYPE_ALIAS": 8,
    "PARAMETER": 1,
    "LOCAL": 1,
}


def _kind_preference(kind: str) -> int:
    return _KIND_PREF.get((kind or "").upper(), 2)


def finalize_rank(rankable: Rankable, query: str) -> tuple:
    """Produce the deterministic sort key for one candidate.

    Rank ordering is (match) then (definition preference) then name/path, so
    repeated searches are fully deterministic and real symbol definitions
    consistently outrank same-named import/export aliases.
    """
    lower = query.casefold()
    base, stanza = rankable._match_score(query, lower)
    kind_pref = _kind_preference(rankable.kind)
    return (
        base,
        stanza,
        kind_pref,
        rankable.name.casefold(),
        rankable.path.replace("\\", "/"),
        rankable.key,
    )


def tiebreak_query() -> str:
    """A synthetic query that reproduces the canonical tie-break ordering."""
    return _TIEBREAK_QUERY
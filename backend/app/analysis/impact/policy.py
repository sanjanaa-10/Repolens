"""Traversal policy for change-impact analysis (Phase 6).

The impact engine BFS expands from changed entities across a whitelist of
relationship types only. Structural relationships (DEFINES) and export
declarations (EXPORTS) are deliberately excluded: callers and importers of an
entity are already reachable through CALLS / IMPORTS edges, and expanding
DEFINES from a changed child would explode the graph with siblings that are not
actually dependent on the change.

Classification is intentionally coarse: DIRECT / POTENTIAL / UNRESOLVED /
EXTERNAL only. No risk scores are ever produced.
"""

TRAVERSED_RELATIONSHIP_TYPES = frozenset(
    {
        "CALLS",
        "REFERENCES",
        "EXTENDS",
        "IMPLEMENTS",
        "TESTS",
        "IMPORTS",
    }
)

SKIPPED_RELATIONSHIP_TYPES = frozenset({"DEFINES", "EXPORTS"})

# Symbol kinds whose children are addressed through the parent node during
# traversal (costumers construct / import / test the class, not individual
# methods), so a changed method must surface classes that reference its class.
CONTAINER_KINDS = frozenset({"CLASS", "INTERFACE"})

# Edge processing priority. Lower wins when a node is simultaneously reachable
# through multiple relationship types; TESTS must dominate so test files are
# classified as tests, not generic potential impact.
EDGE_TYPE_PRIORITY = {
    "TESTS": 0,
    "CALLS": 1,
    "REFERENCES": 2,
    "EXTENDS": 3,
    "IMPLEMENTS": 4,
    "IMPORTS": 5,
}


def edge_priority(relationship_type: str) -> int:
    return EDGE_TYPE_PRIORITY.get(relationship_type, 100)
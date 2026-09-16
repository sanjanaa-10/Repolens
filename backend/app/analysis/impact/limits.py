"""Safety and performance limits for change-impact analysis (Phase 6)."""

# Default maximum traversal depth (BFS levels below changed entities).
MAX_IMPACT_DEPTH_DEFAULT: int = 2

# Hard ceiling enforced by API validation and the engine.
MAX_IMPACT_DEPTH_CEILING: int = 6

# Maximum total nodes allowed in a single impact result graph.
MAX_IMPACT_NODES: int = 1000

# Maximum number of impact paths stored in a single analysis.
MAX_IMPACT_PATHS: int = 200

# Caps on UNRESOLVED / EXTERNAL impact node groups. These lists are derived
# from per-repository edges; without caps a pathological graph could emit tens
# of thousands of nodes even though the traversal registry is bounded.
MAX_IMPACT_UNRESOLVED_NODES: int = 500
MAX_IMPACT_EXTERNAL_NODES: int = 500

# Cap on relationships loaded into memory for one repository. Real repos we
# analyze fall far below this (flask ~7,245; itsdangerous ~1,080).
MAX_RELATIONSHIPS_PER_REPOSITORY: int = 100_000
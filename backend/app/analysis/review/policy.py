"""Phase 7 change-review workflow policy: types, priorities, statuses, limits."""

from app.models.schemas import (
    ReviewEntryStatus,
    ReviewItemStatus,
)

# Supported item categories. Only categories backed by actual Phase 6 findings
# are emitted; AFFECTED_DEPENDENCY is used for resolved IMPORTS groups while
# AFFECTED_CALLER covers behavior relationships.
ITEM_TYPES = (
    "CHANGED_CODE",
    "AFFECTED_CALLER",
    "AFFECTED_DEPENDENCY",
    "AFFECTED_TEST",
    "UNRESOLVED_IMPACT",
    "EXTERNAL_DEPENDENCY",
    "CONFIGURATION_CHANGE",
    "DOCUMENTATION_CHANGE",
)

PRIORITIES = ("REQUIRED", "RECOMMENDED", "INFORMATIONAL")

ITEM_STATUSES = tuple(status.value for status in ReviewItemStatus)
ENTRY_STATUSES = tuple(status.value for status in ReviewEntryStatus)

# Deterministic ordering: priority first, then item type, then emission order.
PRIORITY_ORDER = {"REQUIRED": 0, "RECOMMENDED": 1, "INFORMATIONAL": 2}
ITEM_TYPE_ORDER = {
    "CHANGED_CODE": 0,
    "AFFECTED_CALLER": 1,
    "AFFECTED_DEPENDENCY": 2,
    "AFFECTED_TEST": 3,
    "CONFIGURATION_CHANGE": 4,
    "UNRESOLVED_IMPACT": 5,
    "EXTERNAL_DEPENDENCY": 6,
    "DOCUMENTATION_CHANGE": 7,
}

# Relationships that express direct behavioral dependence. Direct (depth == 1)
# groups through these are REQUIRED; anything deeper is only RECOMMENDED.
BEHAVIOR_VIA = {"CALLS", "REFERENCES", "EXTENDS", "IMPLEMENTS"}
DEPENDENCY_VIA = {"IMPORTS"}
TEST_VIA = "TESTS"

RELATION_LABELS = {
    "CALLS": "callers",
    "REFERENCES": "dependents",
    "EXTENDS": "subclasses",
    "IMPLEMENTS": "implementers",
    "IMPORTS": "dependent files",
    "TESTS": "tests",
}

# A single aggregated item never carries more than this many clickable entries.
# Larger groups are capped and the count is preserved in the item description.
MAX_REVIEW_ENTRIES_PER_ITEM = 200
MAX_REVIEW_NOTES_LENGTH = 4000

# Upper bound on the total number of review items a single review can carry.
# Changed-node groups (impact nodes) are already bounded, but a large change
# could otherwise generate thousands of CHANGED_CODE items. Truncation is
# deterministic (post-sort) and disclosed in the review summary.
MAX_REVIEW_ITEMS = 300

# Deterministic, defensible priority rules. REVIEW priority expresses *workflow*
# importance (what a reviewer should look at first), never production risk.
REQUIRED_ITEM_TYPES = {"CHANGED_CODE", "AFFECTED_TEST", "CONFIGURATION_CHANGE"}


def item_sort(item_type: str, priority: str, index: int) -> int:
    return (
        PRIORITY_ORDER.get(priority, 2) * 1000
        + ITEM_TYPE_ORDER.get(item_type, 8) * 100
        + index
    )


def priority_for_item(item_type: str, group_via: str, all_direct: bool) -> str:
    """Assign a workflow priority to an item.

    - CHANGED_CODE, AFFECTED_TEST, CONFIGURATION_CHANGE -> REQUIRED
    - AFFECTED_CALLER -> REQUIRED only for direct behavior-linked groups
    - DOCUMENTATION_CHANGE -> INFORMATIONAL
    - everything else (AFFECTED_DEPENDENCY, UNRESOLVED_IMPACT,
      EXTERNAL_DEPENDENCY, deeper groups) -> RECOMMENDED
    """
    if item_type in REQUIRED_ITEM_TYPES:
        return "REQUIRED"
    if item_type == "AFFECTED_CALLER":
        if all_direct and group_via in BEHAVIOR_VIA:
            return "REQUIRED"
        return "RECOMMENDED"
    if item_type == "DOCUMENTATION_CHANGE":
        return "INFORMATIONAL"
    return "RECOMMENDED"
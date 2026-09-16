"""Central rules for which directories/files are ignored during discovery.

Kept in one place so every stage of the pipeline (ingestion, and later
parsing/search) reuses exactly the same ignore behavior. Intentionally simple
and conservative: generated/vendor trees are excluded, but test directories
are NOT ignored — tests matter to RepoLens.
"""
from __future__ import annotations

# Directory names that are never analyzed: generated output, caches, vendor
# trees, and VCS metadata.
IGNORED_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        "coverage",
        ".next",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".eggs",
    }
)

# Files that are never analyzed even if they sit in an included directory.
IGNORED_FILENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",  # generated lockfile, huge and noisy
        "poetry.lock",
        "Pipfile.lock",
        "yarn.lock",
        "npm-shrinkwrap.json",
    }
)


def should_ignore_directory(name: str) -> bool:
    return name in IGNORED_DIRECTORIES


def should_ignore_file(name: str) -> bool:
    return name in IGNORED_FILENAMES
"""File category classification for changed files.

Deterministically classifies a repository-relative file path into one of:
SOURCE, TEST, CONFIG, DOCUMENTATION, UNKNOWN.

Uses path-based heuristics — never the file contents.
"""
from __future__ import annotations

from pathlib import PurePosixPath

_TEST_DIR_MARKERS = {"tests", "test", "__tests__", "spec", "__spec__"}
_TEST_PREFIXES = ("test_", "_test.", "_spec.", ".test.", ".spec.")
_TEST_SUFFIXES = (".test.", ".spec.", "_test.py", "_test.js", "_test.ts",
                  "_test.tsx", "_test.jsx", ".test.js", ".test.ts",
                  ".test.tsx", ".test.jsx", ".spec.js", ".spec.ts",
                  ".spec.tsx", ".spec.jsx")

_CONFIG_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "requirements-dev.txt", "requirements-test.txt", "Pipfile",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "tsconfig.json", "tsconfig.node.json", "jsconfig.json",
    "vite.config.ts", "vite.config.js", "vite.config.mjs",
    "webpack.config.js", "webpack.config.ts",
    "rollup.config.js", "rollup.config.ts",
    "babel.config.js", "babel.config.json", ".babelrc",
    "jest.config.js", "jest.config.ts", "vitest.config.ts",
    "eslint.config.js", ".eslintrc", ".eslintrc.js", ".eslintrc.json",
    ".prettierrc", ".prettierrc.json", "prettier.config.js",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "Makefile", "Makefile.am",
    ".env.example", ".env.local",
    "tox.ini", "mypy.ini", ".mypy.ini",
    "ruff.toml", ".ruff.toml",
    "pytest.ini", "conftest.py",
    ".github", ".gitlab-ci.yml",
    "MANIFEST.in", "LICENSE", "LICENSE.md", "LICENSE.txt",
    "COPYING",
}

_DOC_NAMES = {
    "README.md", "README.rst", "README.txt", "README",
    "CONTRIBUTING.md", "CONTRIBUTING.rst",
    "CHANGELOG.md", "CHANGELOG.rst", "CHANGES.md", "CHANGES.rst",
    "HISTORY.md", "HISTORY.rst",
    "AUTHORS.md", "AUTHORS.rst",
    "SECURITY.md", "SECURITY.rst",
}

_DOC_EXTENSIONS = {".md", ".rst", ".txt"}

_SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".c", ".cpp", ".h", ".hpp", ".cs",
    ".java", ".kt", ".kts",
    ".go", ".rs", ".rb", ".php",
    ".swift", ".m", ".mm",
    ".sql", ".graphql", ".gql",
    ".sh", ".bash", ".zsh",
    ".vue", ".svelte",
}


def classify_file(path: str) -> str:
    """Classify a repo-relative file path into SOURCE/TEST/CONFIG/DOCUMENTATION/UNKNOWN."""
    p = PurePosixPath(path)
    name = p.name.lower()
    parts = [part.lower() for part in p.parts]
    parent_names = [part.lower() for part in p.parent.parts] if p.parent != PurePosixPath(".") else []

    # CONFIG: exact name matches
    if name in {n.lower() for n in _CONFIG_NAMES}:
        return "CONFIG"
    # Config directories
    if parts[0] == ".github":
        return "CONFIG"

    # DOCUMENTATION: exact name matches or doc extensions in root
    if name in {n.lower() for n in _DOC_NAMES}:
        return "DOCUMENTATION"
    if p.suffix.lower() in _DOC_EXTENSIONS and len(parts) <= 2:
        return "DOCUMENTATION"

    # TEST: directory markers
    if any(marker in parts or marker in parent_names for marker in _TEST_DIR_MARKERS):
        return "TEST"
    # Test file patterns
    if any(name.startswith(pref) for pref in _TEST_PREFIXES):
        return "TEST"
    if any(name.endswith(suf) for suf in _TEST_SUFFIXES):
        return "TEST"

    # SOURCE: recognized source extension
    if p.suffix.lower() in _SOURCE_EXTENSIONS:
        return "SOURCE"

    return "UNKNOWN"

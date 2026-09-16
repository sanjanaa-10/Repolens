"""Deterministic language detection based on file extension.

Central registry so the parser layer (Phase 2) and the UI consume the same
language names. Unknown extensions produce None — they are recorded but never
analyzed.
"""
from __future__ import annotations

from pathlib import Path

# Extension -> canonical language name (lower-cased extension keys).
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
}

# The languages RepoLens actively analyzes in this phase.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("Python", "JavaScript", "TypeScript")


def detect_language(file_path: str | Path) -> str | None:
    """Return the language for a file path, or None if unsupported."""
    extension = Path(file_path).suffix.lower()
    return LANGUAGE_BY_EXTENSION.get(extension)


def is_supported_language(language: str | None) -> bool:
    return language in SUPPORTED_LANGUAGES
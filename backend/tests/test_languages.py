"""Unit tests for language detection."""
from __future__ import annotations

import pytest

from app.repositories.languages import detect_language, is_supported_language


@pytest.mark.parametrize(
    "path,expected",
    [
        ("app/main.py", "Python"),
        ("src/utils.py", "Python"),
        ("server.js", "JavaScript"),
        ("component.jsx", "JavaScript"),
        ("index.ts", "TypeScript"),
        ("App.tsx", "TypeScript"),
        ("README.md", None),
        ("data.json", None),
        ("Makefile", None),
        ("image.png", None),
        ("no_extension", None),
    ],
)
def test_detect_language(path, expected):
    assert detect_language(path) == expected


def test_detect_is_case_insensitive_extension() -> None:
    assert detect_language("App.PY") == "Python"
    assert detect_language("App.TSX") == "TypeScript"


def test_supported_language_set() -> None:
    assert is_supported_language("Python")
    assert is_supported_language("JavaScript")
    assert is_supported_language("TypeScript")
    assert not is_supported_language("Rust")
    assert not is_supported_language(None)
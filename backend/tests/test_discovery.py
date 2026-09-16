"""Unit tests for file discovery and ignore/filtering rules."""
from __future__ import annotations

from app.repositories.discovery import discover_files
from app.repositories.ignore import should_ignore_directory, should_ignore_file

LIMITS = dict(
    max_file_size_bytes=512 * 1024,
    max_files=10_000,
    max_repo_bytes=200 * 1024 * 1024,
)


def _paths(result) -> set[str]:
    return {f.relative_path for f in result.files}


def test_discovers_source_files(sample_repo) -> None:
    result = discover_files(sample_repo(), **LIMITS)
    paths = _paths(result)
    assert "auth/controller.py" in paths
    assert "auth/service.py" in paths
    assert "web/app.ts" in paths
    assert "web/App.tsx" in paths


def test_includes_test_files(sample_repo) -> None:
    result = discover_files(sample_repo(), **LIMITS)
    assert "tests/test_auth.py" in _paths(result)


def test_excludes_vendor_and_generated_dirs(sample_repo) -> None:
    result = discover_files(sample_repo(), **LIMITS)
    paths = _paths(result)
    assert not any(p.startswith("node_modules") for p in paths)
    assert not any(p.startswith(".git") for p in paths)
    assert not any(p.startswith("__pycache__") for p in paths)
    assert not any(p.startswith(".venv") for p in paths)
    assert not any(p.startswith("dist") for p in paths)


def test_excludes_generated_lockfiles(sample_repo) -> None:
    result = discover_files(sample_repo(), **LIMITS)
    assert "package-lock.json" not in _paths(result)


def test_includes_non_source_files_as_metadata(sample_repo) -> None:
    result = discover_files(sample_repo(), **LIMITS)
    paths = _paths(result)
    assert "README.md" in paths
    assert "pyproject.toml" in paths


def test_language_assignment(sample_repo) -> None:
    result = discover_files(sample_repo(), **LIMITS)
    by_path = {f.relative_path: f.language for f in result.files}
    assert by_path["auth/service.py"] == "Python"
    assert by_path["web/app.ts"] == "TypeScript"
    assert by_path["web/App.tsx"] == "TypeScript"
    assert by_path["README.md"] is None
    assert by_path["pyproject.toml"] is None


def test_analyzable_flag(sample_repo) -> None:
    result = discover_files(sample_repo(), **LIMITS)
    by_path = {f.relative_path: f.analyzable for f in result.files}
    assert by_path["auth/service.py"] is True
    assert by_path["web/App.tsx"] is True
    assert by_path["README.md"] is False


def test_oversized_file_not_analyzable(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "huge.py").write_bytes(b"#" * 100)  # 100 bytes > limit below
    result = discover_files(
        root, max_file_size_bytes=50, max_files=100, max_repo_bytes=10_000
    )
    assert result.files[0].analyzable is False
    assert result.files[0].line_count == 0
    assert result.files[0].size_bytes == 100


def test_line_count(sample_repo) -> None:
    result = discover_files(sample_repo(), **LIMITS)
    by_path = {f.relative_path: f.line_count for f in result.files}
    assert by_path["auth/controller.py"] == 2
    assert by_path["web/App.tsx"] == 3  # import line + blank + export


def test_repo_size_limit(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "big.py").write_bytes(b"#" * 5000)
    from app.repositories.errors import RepositoryTooLarge

    import pytest

    with pytest.raises(RepositoryTooLarge):
        discover_files(root, max_file_size_bytes=4096, max_files=100, max_repo_bytes=1000)


def test_file_count_limit(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for i in range(20):
        (root / f"f{i}.py").write_text("pass\n", encoding="utf-8")

    from app.repositories.errors import TooManyFiles

    import pytest

    with pytest.raises(TooManyFiles):
        discover_files(root, max_file_size_bytes=4096, max_files=5, max_repo_bytes=10**9)


def test_language_bytes_aggregation(sample_repo) -> None:
    result = discover_files(sample_repo(), **LIMITS)
    totals = result.by_language_bytes()
    assert totals.get("Python", 0) > 0
    assert totals.get("TypeScript", 0) > 0


def test_ignore_rules() -> None:
    assert should_ignore_directory("node_modules")
    assert should_ignore_directory(".git")
    assert should_ignore_directory("__pycache__")
    assert not should_ignore_directory("tests")
    assert not should_ignore_directory("src")
    assert should_ignore_file("package-lock.json")
    assert not should_ignore_file("package.json")
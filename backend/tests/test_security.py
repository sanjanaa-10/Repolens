"""Security-focused tests: path traversal, symlink escape, contained joins."""
from __future__ import annotations

import os
import sys

import pytest

from app.repositories.cleanup import safe_remove_workspace
from app.repositories.discovery import (
    PathEscapeError,
    discover_files,
    safe_join,
    safe_relative_path,
)

LIMITS = dict(
    max_file_size_bytes=512 * 1024,
    max_files=10_000,
    max_repo_bytes=200 * 1024 * 1024,
)


@pytest.mark.parametrize(
    "traversal",
    [
        "../secret.txt",
        "../../etc/passwd",
        "..\\secret.txt",
        "src/../../secret.txt",
        "/etc/passwd",
        "C:\\Windows\\system32",
        "dir/..\\..\\evil",
        "a/../../../b",
        "..",
        "",
    ],
)
def test_safe_join_rejects_traversal(tmp_path, traversal: str) -> None:
    with pytest.raises(PathEscapeError):
        safe_join(tmp_path, traversal)


def test_safe_join_accepts_valid_relative(tmp_path) -> None:
    target = tmp_path / "src" / "util.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")
    joined = safe_join(tmp_path, "src/util.py")
    assert joined == target


def test_safe_join_normalizes_backslashes(tmp_path) -> None:
    target = tmp_path / "src" / "util.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")
    joined = safe_join(tmp_path, "src\\util.py")
    assert joined == target


def test_discovery_skips_symlink_escape(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    victim = tmp_path / "victim.py"
    victim.write_text("secret = 1\n", encoding="utf-8")

    try:
        os.symlink(victim, root / "escape.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available on this platform")

    result = discover_files(root, **LIMITS)
    assert all(not f.relative_path.startswith("escape.py") for f in result.files)


def test_discovery_skips_symlinked_directory_escape(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.py").write_text("leak = 1\n", encoding="utf-8")

    try:
        os.symlink(outside, root / "linked")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available on this platform")

    result = discover_files(root, **LIMITS)
    assert _paths(result) == set() or all(
        not p.startswith("linked") for p in _paths(result)
    )


def test_safe_relative_path_rejects_outside_component(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"  # sibling, outside repo root
    outside.write_text("x\n", encoding="utf-8")
    with pytest.raises(PathEscapeError):
        safe_relative_path(root, outside)


def test_discovered_paths_are_relative_and_forward_slashed(sample_repo) -> None:
    result = discover_files(sample_repo(), **LIMITS)
    for f in result.files:
        assert not os.path.isabs(f.relative_path)
        assert "\\" not in f.relative_path
        assert f.relative_path != ".."


def test_hidden_path_traversal_via_ignored_dir(tmp_path) -> None:
    # Even if a malicious repo uses a dot-prefixed dir, discovery stays inside.
    root = tmp_path / "repo"
    root.mkdir()
    evil = root / ".git"
    evil.mkdir()
    (evil / "post-checkout").write_text("#!/bin/sh\nrm -rf /\n", encoding="utf-8")
    result = discover_files(root, **LIMITS)
    assert all(not p.startswith(".git") for p in _paths(result))


def test_safe_remove_workspace_refuses_paths_outside_storage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.repositories.cleanup._storage_root", lambda: tmp_path)
    outside = tmp_path.parent / "evil"
    outside.mkdir(exist_ok=True)
    (outside / "x.txt").write_text("x", encoding="utf-8")
    safe_remove_workspace(outside)
    assert outside.exists(), "path outside the storage root must never be removed"


def test_safe_remove_workspace_deletes_readonly_files(tmp_path, monkeypatch) -> None:
    # Git clones contain read-only files (especially on Windows) which used to
    # make rmtree(ignore_errors=True) silently fail and leak workspaces.
    monkeypatch.setattr("app.repositories.cleanup._storage_root", lambda: tmp_path)
    workspace = tmp_path / "owner__repo__token"
    workspace.mkdir()
    readonly = workspace / "readonly.txt"
    readonly.write_text("x", encoding="utf-8")
    os.chmod(readonly, 0o444)
    (workspace / ".git").mkdir()
    safe_remove_workspace(workspace)
    assert not workspace.exists()


def _paths(result) -> set[str]:
    return {f.relative_path for f in result.files}
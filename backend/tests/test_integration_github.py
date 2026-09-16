"""Integration tests that clone a real public GitHub repository.

These require network access and are excluded from normal pytest runs via the
``integration`` marker (see pytest.ini). Run explicitly with:

    python -m pytest -m integration
"""
from __future__ import annotations

import pytest

from app.repositories.acquisition import acquire_repository


@pytest.mark.integration
def test_clone_real_public_repository(tmp_path) -> None:
    destination = tmp_path / "repo"
    branch, commit = acquire_repository("psf", "requests", destination)
    assert branch
    assert len(commit) == 40
    assert (destination / "setup.py").exists()
    assert (destination / "src" / "requests").is_dir()
    assert (destination / "src" / "requests" / "__init__.py").exists()


@pytest.mark.integration
def test_clone_nonexistent_repository_raises(tmp_path) -> None:
    from app.repositories.errors import RepositoryNotFound

    destination = tmp_path / "nope"
    with pytest.raises(RepositoryNotFound):
        acquire_repository(
            "this-org-does-not-exist-9f8a7b",
            "this-repo-does-not-exist-9f8a7b",
            destination,
        )
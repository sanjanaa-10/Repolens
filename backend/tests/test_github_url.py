"""Unit tests for GitHub URL validation."""
from __future__ import annotations

import pytest

from app.repositories.errors import InvalidRepositoryUrl
from app.repositories.github_url import parse_github_url

VALID = [
    "https://github.com/fastapi/fastapi",
    "https://github.com/psf/requests",
    "https://github.com/owner/repo/",
    "https://github.com/owner/repo.git",
    "https://github.com/owner/repo.git/",
    "https://www.github.com/owner/repo",
]

INVALID = [
    "",
    "not a url",
    "http://github.com/owner/repo",  # http not https
    "https://example.com/owner/repo",  # wrong host
    "https://bitbucket.org/owner/repo",
    "https://github.com/",  # missing path
    "https://github.com/owner",  # missing repo
    "https://github.com/owner/repo/extra",  # too many components
    "https://github.com/owner/repo?ref=main",  # query rejected
    "https://github.com/owner/repo#frag",  # fragment rejected
    "file:///C:/repo",  # local file
    "http://localhost:8000/repo",  # localhost
    "https://localhost/repo",
    "https://127.0.0.1/repo",
    "https://github.com/..//repo",  # traversal
    "https://github.com/owner/..",  # parent path
    None,  # type: ignore[arg-type]
]


def _assert_ref(url: str, owner: str, name: str) -> None:
    ref = parse_github_url(url)
    assert ref.owner == owner
    assert ref.name == name
    assert ref.canonical_url == f"https://github.com/{owner}/{name}"
    assert ref.clone_url == f"https://github.com/{owner}/{name}.git"


@pytest.mark.parametrize("url", VALID)
def test_valid_urls(url: str):
    ref = parse_github_url(url)
    assert ref.owner and ref.name


@pytest.mark.parametrize("url", INVALID)
def test_invalid_urls_raise(url: str):
    with pytest.raises(InvalidRepositoryUrl):
        parse_github_url(url)


def test_normalization() -> None:
    _assert_ref("https://github.com/fastapi/fastapi", "fastapi", "fastapi")
    _assert_ref("https://github.com/fastapi/fastapi/", "fastapi", "fastapi")
    _assert_ref("https://github.com/fastapi/fastapi.git", "fastapi", "fastapi")
    _assert_ref("https://github.com/fastapi/fastapi.git/", "fastapi", "fastapi")
    _assert_ref("https://www.github.com/fastapi/fastapi", "fastapi", "fastapi")


def test_canonical_and_clone_urls() -> None:
    ref = parse_github_url("https://github.com/psf/requests/")
    assert ref.canonical_url == "https://github.com/psf/requests"
    assert ref.clone_url == "https://github.com/psf/requests.git"
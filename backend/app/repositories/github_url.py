"""Validation of public GitHub repository URLs.

RepoLens only ingests public repositories hosted on github.com over HTTPS.
All other sources (file paths, other hosts, http, localhost) are rejected
before any Git operation takes place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.repositories.errors import InvalidRepositoryUrl

_ACCEPTED_HOSTS = {"github.com", "www.github.com"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class GitHubRepoRef:
    """A validated reference to a public GitHub repository."""

    owner: str
    name: str

    @property
    def canonical_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}.git"


def parse_github_url(raw: str) -> GitHubRepoRef:
    """Parse and validate a user-supplied GitHub URL.

    Raises :class:`InvalidRepositoryUrl` for anything that is not a public
    GitHub repository URL.
    """
    if raw is None:
        raise InvalidRepositoryUrl()

    value = raw.strip()
    if not value:
        raise InvalidRepositoryUrl()

    try:
        parsed = urlparse(value)
    except ValueError:
        raise InvalidRepositoryUrl() from None

    # Scheme must be exactly https.
    if parsed.scheme != "https":
        raise InvalidRepositoryUrl()

    # Host must be github.com (www.github.com is normalized).
    if parsed.netloc not in _ACCEPTED_HOSTS:
        raise InvalidRepositoryUrl()

    # Query strings and fragments are ambiguous; reject them.
    if parsed.query or parsed.fragment:
        raise InvalidRepositoryUrl()

    # The path must be exactly owner/repo (optional trailing slash,
    # optional .git suffix).
    pieces = [p for p in parsed.path.split("/") if p]
    if not pieces:
        raise InvalidRepositoryUrl()

    repo = pieces[-1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    if len(pieces) != 2 or not repo:
        raise InvalidRepositoryUrl()

    owner, name = pieces[0], repo

    # Reject traversal-prone or empty-looking names.
    if not _SAFE_NAME.match(owner) or not _SAFE_NAME.match(name):
        raise InvalidRepositoryUrl()
    if name in (".", "..") or owner in (".", ".."):
        raise InvalidRepositoryUrl()

    return GitHubRepoRef(owner=owner, name=name)
"""Repository ingestion error taxonomy.

Every failure mode of the ingestion pipeline is represented by an exception
class carrying a user-facing message, an HTTP status code, and a stable code
string. Technical details belong in server logs, never in these messages.
"""
from __future__ import annotations


class RepositoryIngestionError(Exception):
    """Base class for all controlled ingestion failures."""

    code: str = "ingestion_error"
    status_code: int = 500
    default_message: str = "Repository ingestion failed."

    def __init__(self, message: str | None = None, *, cause: Exception | None = None):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.cause = cause


class InvalidRepositoryUrl(RepositoryIngestionError):
    code = "invalid_url"
    status_code = 400
    default_message = (
        "Invalid repository URL. Provide a public GitHub repository URL such as "
        "https://github.com/owner/repository"
    )


class RepositoryNotFound(RepositoryIngestionError):
    code = "repository_not_found"
    status_code = 404
    default_message = (
        "We couldn't find this repository on GitHub. Check that the URL is "
        "correct and that the repository is public."
    )


class RepositoryAccessError(RepositoryIngestionError):
    code = "repository_unavailable"
    status_code = 502
    default_message = (
        "We couldn't access this repository on GitHub. It may be private, "
        "rate-limited, or temporarily unavailable."
    )


class RepositoryTooLarge(RepositoryIngestionError):
    code = "repository_too_large"
    status_code = 413
    default_message = (
        "This repository exceeds the size limit RepoLens can analyze. "
        "Try a smaller repository or a shallow subset."
    )


class TooManyFiles(RepositoryIngestionError):
    code = "too_many_files"
    status_code = 413
    default_message = (
        "This repository contains more files than RepoLens can index in one "
        "run. Try a smaller repository."
    )


class CloneTimeoutError(RepositoryIngestionError):
    code = "clone_timeout"
    status_code = 504
    default_message = (
        "Cloning this repository took too long and timed out. It may be very "
        "large or GitHub may be slow right now."
    )


class UnsupportedRepository(RepositoryIngestionError):
    code = "unsupported_repository"
    status_code = 422
    default_message = (
        "This repository contains no supported source files. RepoLens "
        "currently analyzes Python, JavaScript, and TypeScript."
    )


class GitUnavailable(RepositoryIngestionError):
    code = "git_unavailable"
    status_code = 500
    default_message = (
        "The analysis service could not run Git. Contact the administrator."
    )


class InternalIngestionError(RepositoryIngestionError):
    code = "internal_error"
    status_code = 500
    default_message = "Repository ingestion failed unexpectedly. Please try again."
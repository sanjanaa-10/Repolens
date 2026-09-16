"""Safe Git subprocess execution for diff analysis.

Runs git commands as argument arrays (never shell=True) against the stored
repository working tree. All user-provided revisions are validated before use.
Environment is controlled to prevent external diff drivers, hooks, aliases,
and pager invocation.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.analysis.diff import limits
from app.core.git_safety import (
    GitSafetyError,
    run_git_capture,
    safe_env,
)

logger = logging.getLogger("repolens.diff.git")


class GitError(Exception):
    """Raised when a git command fails or returns unexpected output."""

    def __init__(self, message: str, *, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


class RevisionNotFoundError(GitError):
    """Raised when a revision cannot be resolved in the repository."""


class RevisionValidationError(GitError):
    """Raised when a revision string fails safety validation."""


@dataclass(frozen=True)
class FileChange:
    """One file from git diff --numstat output."""
    path: str
    additions: int
    deletions: int
    status: str  # A/D/M/R/C/T
    old_path: str | None = None
    binary: bool = False


def _validate_revision(revision: str) -> None:
    """Reject malformed, injection-prone, or empty revision strings."""
    if not revision or not revision.strip():
        raise RevisionValidationError("Revision must not be empty.")
    rev = revision.strip()
    if len(rev) > limits.MAX_REVISION_LENGTH:
        raise RevisionValidationError(
            f"Revision exceeds maximum length ({limits.MAX_REVISION_LENGTH})."
        )
    # Block option injection
    if rev.startswith("-"):
        raise RevisionValidationError("Revision must not start with '-'.")
    # Block shell metacharacters
    if re.search(r'[;&|`$(){}!\n\r]', rev):
        raise RevisionValidationError("Revision contains invalid characters.")
    # Block path traversal
    if ".." in rev:
        raise RevisionValidationError("Revision must not contain '..'.")
    # Allow only safe ref characters: letters, digits, and common ref
    # separators. This covers hex SHAs, branch names, tags, HEAD~n, and
    # refs/path-style names. The leading '-' and `..` checks above prevent
    # option injection and range syntax; shell metacharacters are rejected.
    if not re.match(r'^[A-Za-z0-9._/~^@*-]+$', rev):
        raise RevisionValidationError(
            "Revision contains disallowed characters. "
            "Use hex SHA, branch name, or tag."
        )


def run_git(args: list[str], *, cwd: Path, timeout: int | None = None) -> str:
    """Execute a git command with a controlled environment.

    Args:
        args: git command arguments (e.g. ["diff", "--numstat", "abc..def"]).
        cwd: repository working tree path.
        timeout: override timeout in seconds.

    Returns:
        Combined stdout output as a string.

    Raises:
        GitError: on non-zero exit, timeout, output-cap violation, or other
        failure.
    """
    timeout = timeout or limits.MAX_GIT_TIMEOUT_SECONDS
    logger.debug("git %s (cwd=%s)", " ".join(args), cwd)

    try:
        return run_git_capture(args, cwd=cwd, timeout=timeout, env=safe_env())
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"Git command timed out after {timeout}s.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise GitError(
            f"Git command failed (exit {exc.returncode}): {stderr}",
            stderr=stderr,
        ) from exc
    except GitSafetyError as exc:
        raise GitError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise GitError("Git executable not found.") from exc


def validate_revision_in_repo(revision: str, cwd: Path) -> str:
    """Validate a revision string and resolve it to a full SHA.

    Returns the resolved SHA, or raises RevisionNotFoundError/RevisionValidationError.
    """
    _validate_revision(revision)
    try:
        sha = run_git(["rev-parse", "--verify", revision.strip()], cwd=cwd)
        return sha.strip()
    except GitError:
        raise RevisionNotFoundError(
            f"Revision '{revision}' could not be resolved in this repository."
        )


def ensure_revision_resolvable(revision: str, cwd: Path) -> bool:
    """Return True if the revision resolves in the repository (without raising).

    The revision is validated *before* any git invocation so that option
    injection is impossible. Never mutates the repository.
    """
    _validate_revision(revision)
    try:
        sha = run_git(["rev-parse", "--verify", revision.strip()], cwd=cwd)
        return bool(sha.strip())
    except GitError:
        return False


def deepen_for_revision(revision: str, cwd: Path, *, depth: int = 2) -> str:
    """Bring a revision into the local repository via a bounded fetch.

    Shallow-cloned repositories (--depth 1) only know HEAD. To diff two real
    commits we fetch the requested revision together with up to `depth` parent
    commits. The fetch is bounded, non-interactive, and read-only with respect
    to the working tree (no checkout).

    For traversal expressions like ``HEAD~1`` or ``main^``, the expression is
    not a valid fetch refspec. In this case we resolve the local ``HEAD`` and
    deepen around that commit so the expression's ancestors become available.

    Returns the resolved full SHA if the revision is now available locally.

    Raises:
        RevisionValidationError: if the revision fails safety validation.
        RevisionNotFoundError: if the revision still cannot be resolved.
    """
    _validate_revision(revision)
    # Traversal expressions (HEAD~1, main^2, etc.) are not fetchable
    # refspecs. Deepen around the local HEAD so the ancestors of the
    # expression become available, then resolve the expression itself.
    if "~" in revision or "^" in revision:
        head_sha = validate_revision_in_repo("HEAD", cwd=cwd)
        run_git(
            ["fetch", "--depth", str(depth), "origin", head_sha],
            cwd=cwd,
        )
    else:
        run_git(
            ["fetch", "--depth", str(depth), "origin", revision.strip()],
            cwd=cwd,
        )
    return validate_revision_in_repo(revision, cwd=cwd)


def _expand_numstat_path(path: str) -> str:
    """Translate git's rename brace notation into the concrete new path.

    ``auth/{util.py => utils.py}`` -> ``auth/utils.py``
    ``{a => b/c}.py`` -> ``b/c.py``
    Any path without braces is returned unchanged.
    """
    if "{" not in path:
        return path
    head, tail = path.split("{", 1)
    if "}" not in tail:
        return path
    inner, suffix = tail.split("}", 1)
    if " => " not in inner:
        return path
    _old, new = inner.split(" => ", 1)
    return head + new + suffix


def get_numstat(base: str, head: str, cwd: Path) -> list[FileChange]:
    """Get per-file insertion/deletion counts and statuses for a diff.

    ``--name-status`` is authoritative for paths and status codes (including
    rename/copy source paths); ``--numstat`` supplies the line counts. Renames
    are correlated by translating numstat's brace notation to the new path.

    Returns a list of FileChange objects sorted by new path.
    """
    # --diff-filter=ACDMRT handles all standard statuses.
    # --no-ext-diff prevents external diff drivers.
    ns_output = run_git(
        ["diff", "--no-ext-diff", "--name-status", "--diff-filter=ACDMRT",
         "--find-renames", "--find-copies", base, head],
        cwd=cwd,
    )

    num_output = run_git(
        ["diff", "--no-ext-diff", "--numstat", "--diff-filter=ACDMRT",
         "--find-renames", "--find-copies", base, head],
        cwd=cwd,
    )
    # numstat keyed by the concrete new path.
    counts: dict[str, tuple[str, str]] = {}
    for line in num_output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        counts[_expand_numstat_path(parts[2].strip())] = (parts[0], parts[1])

    changes: list[FileChange] = []
    for line in ns_output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0][0]
        if code in ("R", "C") and len(parts) >= 3:
            old_path, new_path = parts[1].strip(), parts[2].strip()
        else:
            new_path = parts[1].strip()
            old_path = None

        status = {
            "A": "ADDED",
            "D": "DELETED",
            "M": "MODIFIED",
            "R": "RENAMED",
            "C": "COPIED",
            "T": "TYPE_CHANGED",
        }.get(code, "MODIFIED")

        add_str, del_str = counts.get(new_path, ("0", "0"))
        binary = add_str == "-" or del_str == "-"
        additions = 0 if binary else int(add_str)
        deletions = 0 if binary else int(del_str)

        changes.append(FileChange(
            path=new_path,
            additions=additions,
            deletions=deletions,
            status=status,
            old_path=old_path,
            binary=binary,
        ))

    return sorted(changes, key=lambda c: c.path)


def get_diff_output(base: str, head: str, cwd: Path, path: str) -> str:
    """Get the unified diff output for a single file."""
    output = run_git(
        ["diff", "--no-ext-diff", "--no-color", "--unified=3",
         "--diff-filter=ACDMRT", base, head, "--", path],
        cwd=cwd,
    )
    return output


def resolve_revision_or_head(revision: str | None, cwd: Path) -> str:
    """If revision is None, return HEAD. Otherwise validate and resolve."""
    if revision is None or revision.strip() == "":
        return validate_revision_in_repo("HEAD", cwd=cwd)
    return validate_revision_in_repo(revision, cwd=cwd)

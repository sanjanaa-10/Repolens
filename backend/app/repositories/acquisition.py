"""Controlled Git acquisition of public GitHub repositories.

The repository URL is validated *before* this module is ever called, and Git
is invoked exclusively through argument arrays — never through a shell and
never with user-provided command fragments. Only the validated clone URL is
passed to Git.

The cloned working tree is left on disk for the current session; callers are
responsible for cleaning it up (see :mod:`app.repositories.cleanup`).
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.config import get_settings
from app.core.git_safety import (
    GitSafetyError,
    run_git_capture,
    safe_env,
)
from app.repositories.discovery import PathEscapeError, safe_join
from app.repositories.errors import (
    CloneTimeoutError,
    GitUnavailable,
    RepositoryAccessError,
    RepositoryNotFound,
)

logger = logging.getLogger("repolens.acquisition")


def _run_git(args: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    try:
        stdout = run_git_capture(args, cwd=cwd, timeout=timeout, env=safe_env())
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
    except FileNotFoundError:
        raise GitUnavailable() from None
    except GitSafetyError as exc:
        # Resource-safety bound (e.g. output cap): surface as a generic failure.
        return subprocess.CompletedProcess(args, 1, stdout="", stderr=str(exc))
    except subprocess.TimeoutExpired:
        raise CloneTimeoutError() from None
    except subprocess.CalledProcessError as exc:
        return subprocess.CompletedProcess(
            exc.returncode,
            args,
            stdout=exc.output or "",
            stderr=exc.stderr or "",
        )


def _run_with_timeout(args: list[str], *, cwd: Path, description: str) -> subprocess.CompletedProcess:
    try:
        return _run_git(args, cwd=cwd, timeout=get_settings().clone_timeout_seconds)
    except subprocess.TimeoutExpired:
        logger.warning("git operation timed out: %s", description)
        raise CloneTimeoutError() from None


def _classify_clone_failure(url: str, stderr: str) -> RepositoryAccessError:
    lower = stderr.lower()
    not_found_markers = (
        "repository not found",
        "not found",
        "does not appear to be a git repository",
        "could not read from remote repository",
        "access denied",
        "doesn't exist",
    )
    if any(marker in lower for marker in not_found_markers):
        logger.info("repository not found on GitHub: %s", url)
        return RepositoryNotFound()
    logger.warning("clone failed for %s: %s", url, stderr.strip()[-500:])
    return RepositoryAccessError()


def acquire_repository(owner: str, name: str, destination: Path) -> tuple[str, str]:
    """Clone ``owner/name`` into ``destination``.

    Returns ``(branch, commit_sha)`` from the resulting working tree.
    """
    if not _git_available():
        raise GitUnavailable()

    clone_url = f"https://github.com/{owner}/{name}.git"
    logger.info("clone started: %s", clone_url)

    result = _run_with_timeout(
        ["clone", "--depth", "1", "--single-branch", clone_url, str(destination)],
        cwd=destination.parent,
        description=f"clone {clone_url}",
    )

    if result.returncode != 0:
        raise _classify_clone_failure(clone_url, result.stderr or "")

    logger.info("clone completed: %s", clone_url)

    branch = _rev_parse(destination, "--abbrev-ref", "HEAD")
    commit = _rev_parse(destination, "HEAD")
    return branch, commit


def _rev_parse(repo: Path, *args: str) -> str | None:
    result = _run_with_timeout(
        ["rev-parse", *args],
        cwd=repo,
        description=f"rev-parse {args}",
    )
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _git_available() -> bool:
    result = _run_git(["version"], cwd=Path.cwd(), timeout=10)
    return result.returncode == 0


def repo_working_tree_size(repo: Path) -> int:
    """Total size of tracked files in the working tree (bytes).

    Uses `git ls-files` so we measure only versioned content, ignoring
    untracked debris that may exist in the clone.
    """
    result = _run_with_timeout(
        ["ls-files", "-z"],
        cwd=repo,
        description="ls-files",
    )
    if result.returncode != 0:
        return 0
    total = 0
    for raw in (result.stdout or "").split("\x00"):
        if not raw:
            continue
        try:
            total += safe_join(repo, raw).stat().st_size
        except (OSError, PathEscapeError):
            continue
    return total
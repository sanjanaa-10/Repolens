"""Safe cleanup of cloned repositories.

Removal is guarded so a bug can never delete arbitrary server paths: the
target must resolve inside the configured repository storage directory.

Git clones routinely contain read-only files (the repository objects and
caches are typically flagged read-only, especially on Windows), so removal
clears the read-only attribute before deleting and retries on access errors.
"""
from __future__ import annotations

import logging
import os
import stat
import shutil
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger("repolens.cleanup")


def _storage_root() -> Path:
    return get_settings().repo_storage_dir.resolve()


def _clear_readonly(func, path: str, exc_info) -> None:
    """shutil.rmtree error handler: make a path writable, then retry."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        logger.warning("could not remove path during cleanup: %s", path[:300])


def _remove_tree(resolved: Path) -> None:
    shutil.rmtree(resolved, onerror=_clear_readonly)


def safe_remove_workspace(target: Path) -> None:
    """Delete a cloned repository directory if it lives in storage."""
    root = _storage_root()
    resolved = Path(target).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        logger.error("refusing to remove path outside storage root: %s", resolved)
        return
    if resolved.exists():
        _remove_tree(resolved)
        if not resolved.exists():
            logger.info("cleaned workspace: %s", resolved.name)
        else:
            logger.error("workspace still present after cleanup: %s", resolved)
"""Limits for diff analysis to prevent unbounded computation.

These caps protect against huge diffs: maximum files, hunks, changed lines,
and raw diff output size per request.
"""
from __future__ import annotations

MAX_DIFF_FILES = 500
MAX_DIFF_HUNKS = 5000
MAX_DIFF_CHANGED_LINES = 50000
MAX_DIFF_OUTPUT_BYTES = 16 * 1024 * 1024  # 16 MB raw git diff output
MAX_GIT_TIMEOUT_SECONDS = 60
MAX_REVISION_LENGTH = 128

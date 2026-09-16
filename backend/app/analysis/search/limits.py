"""Search limits that bound query cost for untrusted input.

These caps keep every search request bounded in CPU and memory: query length,
result pagination, candidate scans, text scan byte budget, and per-category
group sizes in investigation responses.
"""
from __future__ import annotations

MAX_QUERY_LENGTH = 200
MAX_RESULT_LIMIT = 100
MAX_OFFSET = 10000

# Candidate scans (symbol/file searches rank in-memory after a scoped query).
MAX_SYMBOL_CANDIDATES = 5000
MAX_FILE_CANDIDATES = 2000

# Text search budget. Files are read one at a time (never the whole repository
# at once) up to this total scanned-byte ceiling per request.
MAX_TEXT_SCAN_BYTES = 32 * 1024 * 1024
MAX_TEXT_COLLECT = 1000
MAX_TEXT_MATCHES_PER_FILE = 200
MAX_FILE_SOURCE_BYTES = 512 * 1024
MAX_SNIPPET_LENGTH = 220

# Investigation response caps.
MAX_GROUP_EDGES = 200
MAX_RELATED_FILES = 100
MAX_SNIPPET_LINES = 300
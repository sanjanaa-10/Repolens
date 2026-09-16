"""Shared secret redaction for RepoLens (Phase 9).

Source text, repository metadata, and Lens traffic are untrusted data. Anything
that eventually reaches a log line, a persisted review item, an LLM provider, or
an HTTP response must first pass through :func:`redact_text` so credential-shaped
fragments cannot leak. Reference values like ``api_key`` inside config metadata
are intentionally preserved (see ``redact_secret`` docstring).
"""
from __future__ import annotations

import re

_SECRET_KEY = re.compile(
    r"(?i)(api[_-]?key|password|passwd|pwd|token|secret|private[_-]?key)"
)

# Matches assignments such as  API_KEY="x",  PASSWORD =  hunter2,  secret:xx,
# SECRET_KEY := xyzzy. ``[:=]+`` accepts ``=``, ``:``, and ``:=`` separators.
# ``secret[_-]?key`` must be tried before ``secret`` so the compound key is
# consumed as a unit.
_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|password|passwd|secret[_-]?key|secret|token|private[_-]?key)"
    r"\s*[:=]+\s*[\"']?[^\"'\s,;}]+[\"']?"
)

_REDACT_KEYS = (
    "API_KEY",
    "APISECRET",
    "ACCESS_TOKEN",
    "AUTH_TOKEN",
    "AUTHORIZATION",
    "SECRET",
    "SECRET_KEY",
    "PRIVATE_KEY",
    "PASSWORD",
    "PASSWD",
    "CREDENTIALS",
    "COOKIE",
)

_REDACT_RE = re.compile(
    r"(?i)(%s)\s*[:=]\s*([\"']?[^\"'\s,;}]+[\"']?)" % "|".join(_REDACT_KEYS)
)


def is_secret_key(key: str) -> bool:
    """Return whether a config/metadata key looks like a secret.

    Used to decide what may be stored/persisted/returned in metadata payloads
    (e.g. ``EnvConfigMeta`` uses it to exclude reference values).
    """
    return bool(_SECRET_KEY.search(key or ""))


def redact_secret(text: str) -> str:
    """Redact credential-looking assignments anywhere in a string.

    Only *values* are redacted. A bare key such as ``api_key`` in a config
    reference list is a label, not a credential, so a key without ``=``/``:``
    is left intact. This keeps ``EnvConfigMeta`` entries readable for document
    config files without ever emitting their values.
    """
    if not text:
        return text
    return _ASSIGNMENT.sub(lambda m: f"{m.group(1)}=REDACTED", text)


def redact_text(text: str) -> str:
    """Redact assignment-shaped secrets and control characters in one pass.

    Control characters are stripped before redaction so a value cannot smuggle
    a newline past the assignment regex or corrupt log lines/JSON.
    """
    if not text:
        return ""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]", "", text)
    return redact_secret(cleaned)


def redact_log_line(text: str) -> str:
    """Redact wide ``KEY=value`` patterns used before writing log lines."""
    if not text:
        return text
    return _REDACT_RE.sub(lambda m: f"{m.group(1)}=REDACTED", text)


# Back-compat alias used by the review pipeline.
clean_text = redact_text
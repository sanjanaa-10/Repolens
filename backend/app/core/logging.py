"""Structured application logging configuration.

Logs include timestamp, level, module, and message. Never log secrets or
repository source contents — only counts, URLs, and error categories.
"""
from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"


def configure_logging(debug: bool = False) -> None:
    root = logging.getLogger()
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # Keep uvicorn/access logs from drowning RepoLens logs.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"repolens.{name}")
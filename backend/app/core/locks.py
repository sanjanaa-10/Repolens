"""Bounded, per-key async locks to serialize state-changing operations.

RepoLens is a single-process FastAPI app. Two concurrent requests for the same
repository/diff/review could otherwise both try to create the same row (unique
constraints) or delete the same workspace, producing 500s or worse, silent
races. These locks serialize work *per key* so independent repos run in
parallel but duplicate work on the same key waits its turn instead of
crashing.

The registry is bounded (LRU with a hard cap) because keys are derived from
user-supplied repository identifiers.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections import OrderedDict
from typing import AsyncIterator

_MAX_KEYS = 256

_registry: OrderedDict[str, asyncio.Lock] = OrderedDict()
_guard = asyncio.Lock()


async def _get_lock(key: str) -> asyncio.Lock:
    async with _guard:
        lock = _registry.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _registry[key] = lock
            if len(_registry) > _MAX_KEYS:
                oldest = next(iter(_registry))
                del _registry[oldest]
        else:
            _registry.move_to_end(key)
        return lock


@contextlib.asynccontextmanager
async def protected(key: str) -> AsyncIterator[None]:
    """Acquire the lock for ``key``, yield, then release it."""
    lock = await _get_lock(key)
    async with lock:
        yield


def reset_locks() -> None:
    """Drop all held locks (used by tests to clear state between runs)."""
    _registry.clear()
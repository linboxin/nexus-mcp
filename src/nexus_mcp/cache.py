"""A small async-aware TTL cache.

Values are stored with the time they were fetched so callers can tell the user
when data is cached rather than live (important for grades and submission
status, which default to a 30 s TTL and are always labelled).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Hashable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CacheMeta:
    cached: bool
    fetched_at: float
    ttl: int


class TTLCache:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._entries: dict[Hashable, tuple[Any, float, int]] = {}
        self._locks: dict[Hashable, asyncio.Lock] = {}

    def get(self, key: Hashable) -> tuple[Any, float, int] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        _value, fetched_at, ttl = entry
        if self._clock() - fetched_at >= ttl:
            self._entries.pop(key, None)
            return None
        return entry

    def set(self, key: Hashable, value: Any, ttl: int) -> None:
        if ttl > 0:
            self._entries[key] = (value, self._clock(), ttl)

    async def get_or_fetch(
        self, key: Hashable, ttl: int, fetch: Callable[[], Awaitable[T]]
    ) -> tuple[T, CacheMeta]:
        if ttl <= 0:
            value = await fetch()
            return value, CacheMeta(cached=False, fetched_at=self._clock(), ttl=0)
        hit = self.get(key)
        if hit is not None:
            value, fetched_at, entry_ttl = hit
            return value, CacheMeta(cached=True, fetched_at=fetched_at, ttl=entry_ttl)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self.get(key)
            if hit is not None:
                value, fetched_at, entry_ttl = hit
                return value, CacheMeta(cached=True, fetched_at=fetched_at, ttl=entry_ttl)
            value = await fetch()
            now = self._clock()
            self.set(key, value, ttl)
            return value, CacheMeta(cached=False, fetched_at=now, ttl=ttl)

    def invalidate(self, prefix: Hashable | None = None) -> None:
        if prefix is None:
            self._entries.clear()
            return
        for key in list(self._entries):
            if isinstance(key, tuple) and key and key[0] == prefix:
                self._entries.pop(key, None)

    def __len__(self) -> int:
        return len(self._entries)

# app/page_cache.py
from __future__ import annotations

import os
import time
import asyncio
from collections import OrderedDict
from typing import NamedTuple, Dict, Tuple, Optional


class PageKey(NamedTuple):
    """Structured cache key for a paged /characters response."""

    sort: str
    order: str
    page: int
    page_size: int


class PageCache:
    """Tiny per-pod LRU+TTL cache with per-key singleflight locks.

    Stores full /characters responses keyed by (sort, order, page, page_size).
    """

    def __init__(self, ttl: float, capacity: int) -> None:
        """Initialize the cache.

        Args:
            ttl: Time-to-live in seconds for cached entries.
            capacity: Maximum number of page entries to store (LRU-evicted).
        """
        self._ttl = ttl
        self._cap = capacity
        self._store: "OrderedDict[PageKey, Tuple[float, Dict]]" = OrderedDict()
        self._locks: Dict[PageKey, asyncio.Lock] = {}

    def key(self, sort: str, order: str, page: int, page_size: int) -> PageKey:
        """Build a structured key for a page."""
        return PageKey(sort, order, page, page_size)

    def get(self, key: PageKey) -> Optional[Dict]:
        """Return cached value if fresh; otherwise evict and return None."""
        ts_val = self._store.get(key)
        if not ts_val:
            return None
        ts, val = ts_val
        if time.time() - ts > self._ttl:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)  # LRU bump
        return val

    def put(self, key: PageKey, value: Dict) -> None:
        """Insert or refresh a cache entry and enforce LRU capacity."""
        self._store[key] = (time.time(), value)
        self._store.move_to_end(key)
        while len(self._store) > self._cap:
            self._store.popitem(last=False)

    def invalidate_all(self) -> None:
        """Clear the entire page cache."""
        self._store.clear()

    def lock_for(self, key: PageKey) -> asyncio.Lock:
        """Return the per-key singleflight lock (create if absent)."""
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def stats(self) -> Dict[str, int]:
        """Return simple stats for observability."""
        return {"size": len(self._store), "capacity": self._cap}


# Singleton configured from env (tweak via PAGE_CACHE_TTL / PAGE_CACHE_MAX)
_PAGE_TTL = float(os.getenv("PAGE_CACHE_TTL", "30"))
_PAGE_MAX = int(os.getenv("PAGE_CACHE_MAX", "256"))
page_cache = PageCache(ttl=_PAGE_TTL, capacity=_PAGE_MAX)

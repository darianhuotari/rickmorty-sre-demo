import os
from cachetools import TTLCache
from typing import Any, Tuple

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))
CACHE_MAXSIZE = int(os.getenv("CACHE_MAXSIZE", "1024"))

# cache for serialized query results (e.g., list endpoints)
_query_cache = TTLCache(maxsize=CACHE_MAXSIZE, ttl=CACHE_TTL_SECONDS)
_hits = 0
_misses = 0

def cache_get(key: Tuple[Any, ...]):
    global _hits, _misses
    if key in _query_cache:
        _hits += 1
        return _query_cache[key]
    _misses += 1
    return None

def cache_set(key: Tuple[Any, ...], value):
    _query_cache[key] = value

def cache_stats():
    return {"size": len(_query_cache), "hits": _hits, "misses": _misses, "ttl_seconds": CACHE_TTL_SECONDS}
from __future__ import annotations

import asyncio
from typing import Dict, Any

import pytest

from app.db import get_session
from app import ingest, api, crud
from app.page_cache import PageCache, PageKey

import app.main as main


def test_page_key_is_structured_and_hashable():
    """Verify PageKey structure, equality, and hashability.

    Ensures:
        * The tuple-like key preserves field types and order.
        * Keys can be used in dictionaries/sets without collisions.
    """
    k1 = PageKey("id", "asc", 1, 20)
    k2 = PageKey("id", "asc", 1, 20)
    k3 = PageKey("name", "asc", 1, 20)

    d = {k1: "a"}
    assert d[k2] == "a"  # equality & hash
    assert k1 != k3
    assert isinstance(k1.page, int) and isinstance(k1.page_size, int)


def test_put_get_and_ttl_expiration(monkeypatch):
    """get() returns fresh entries and evicts expired ones based on TTL.

    Steps:
        1) Freeze time; insert one entry with TTL=1s.
        2) Within TTL, get() returns the value.
        3) After TTL, get() returns None and entry is evicted.
    """
    from app import page_cache as mod

    cache = PageCache(ttl=1.0, capacity=10)
    key = cache.key("id", "asc", 1, 20)

    # Controlled clock
    now = {"t": 1_000.0}
    monkeypatch.setattr(mod.time, "time", lambda: now["t"])

    # Put and get (fresh)
    payload = {"ok": True}
    cache.put(key, payload)
    assert cache.get(key) == payload

    # Still fresh at +0.5s
    now["t"] += 0.5
    assert cache.get(key) == payload

    # Expired at +2.0s -> evicted on access
    now["t"] += 2.0
    assert cache.get(key) is None

    # Size reflects eviction
    assert cache.stats()["size"] == 0


def test_lru_capacity_eviction_and_bump(monkeypatch):
    """LRU capacity: inserting beyond capacity evicts the least-recently-used entry.

    Also verifies that a get() bumps recency so a recently accessed key is retained.
    """
    from app import page_cache as mod

    cache = PageCache(ttl=60.0, capacity=2)
    k1 = cache.key("id", "asc", 1, 20)
    k2 = cache.key("id", "asc", 2, 20)
    k3 = cache.key("id", "asc", 3, 20)

    # Stable time
    monkeypatch.setattr(mod.time, "time", lambda: 1000.0)

    cache.put(k1, {"p": 1})
    cache.put(k2, {"p": 2})

    # Bump k1 to be most-recent
    assert cache.get(k1) == {"p": 1}

    # Insert k3 -> evict LRU (k2), keep k1
    cache.put(k3, {"p": 3})

    assert cache.get(k1) == {"p": 1}
    assert cache.get(k2) is None  # evicted
    assert cache.get(k3) == {"p": 3}

    # Capacity honored
    assert cache.stats()["size"] == 2
    assert cache.stats()["capacity"] == 2


def test_invalidate_all_clears_cache():
    """invalidate_all() removes all entries and resets size."""
    cache = PageCache(ttl=60.0, capacity=10)
    k1 = cache.key("id", "asc", 1, 20)
    k2 = cache.key("name", "desc", 1, 50)

    cache.put(k1, {"a": 1})
    cache.put(k2, {"b": 2})
    assert cache.stats()["size"] == 2

    cache.invalidate_all()
    assert cache.get(k1) is None
    assert cache.get(k2) is None
    assert cache.stats()["size"] == 0


@pytest.mark.asyncio
async def test_singleflight_lock_ensures_one_fill(monkeypatch):
    """Concurrent misses for the same key should execute the fill exactly once.

    Emulates the route behavior:
        * All tasks miss the cache initially.
        * They contend on the same per-key lock.
        * Exactly one task performs the "expensive" fill and put().
        * All tasks receive the same cached value.
    """
    cache = PageCache(ttl=60.0, capacity=10)
    key = cache.key("id", "asc", 1, 20)

    calls = {"n": 0}
    produced_value: Dict[str, Any] = {"filled": True, "v": 42}

    async def worker():
        # Fast-path
        v = cache.get(key)
        if v is not None:
            return v

        # Singleflight
        lock = cache.lock_for(key)
        async with lock:
            # Re-check after acquiring
            v = cache.get(key)
            if v is not None:
                return v

            # Perform "expensive" work exactly once
            calls["n"] += 1
            await asyncio.sleep(0.01)  # simulate I/O
            cache.put(key, produced_value)
            return produced_value

    # Fire a bunch of concurrent requests
    results = await asyncio.gather(*[worker() for _ in range(8)])

    # Only one fill
    assert calls["n"] == 1

    # Everyone saw the same cached value
    assert all(r is produced_value or r == produced_value for r in results)
    assert cache.get(key) == produced_value


@pytest.mark.asyncio
async def test_locks_differ_for_distinct_keys_and_reuse_for_same_key():
    """lock_for() returns the same lock per key and different locks across keys."""
    cache = PageCache(ttl=60.0, capacity=10)
    a1 = cache.key("id", "asc", 1, 20)
    a2 = cache.key("id", "asc", 1, 20)  # same logical key
    b = cache.key("id", "asc", 2, 20)  # different key

    lock_a_first = cache.lock_for(a1)
    lock_a_second = cache.lock_for(a2)
    lock_b = cache.lock_for(b)

    assert lock_a_first is lock_a_second
    assert lock_a_first is not lock_b


@pytest.mark.asyncio
async def test_initial_sync_invalidation_failure_is_swallowed(monkeypatch):
    """Initial sync: a failure in page_cache.invalidate_all() must not bubble.

    We simulate an empty table (count=0), force a successful upsert (n=1),
    and then make invalidate_all() raise. The function should still return 1.
    """

    # Make table appear empty
    async def _count(_session):
        return 0

    monkeypatch.setattr(crud, "count_characters", _count)

    # Stub upstream + filtering
    async def _fetch():
        return [
            {
                "id": 1,
                "name": "Morty",
                "status": "Alive",
                "species": "Human",
                "origin": {"name": "Earth (C-137)"},
                "image": None,
                "url": "",
            }
        ]

    def _filter(raw):  # your api.filter_character_results flattens origin to string
        return [
            {
                "id": 1,
                "name": "Morty",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (C-137)",
                "image": None,
                "url": "",
            }
        ]

    monkeypatch.setattr(api, "fetch_all_characters", _fetch)
    monkeypatch.setattr(api, "filter_character_results", _filter)

    # Upsert succeeds and returns >0
    async def _upsert(_session, _items):
        return 1

    monkeypatch.setattr(crud, "upsert_characters", _upsert)

    # Invalidation throws (exercise the except block)
    def _bomb():
        raise RuntimeError("boom")

    monkeypatch.setattr(ingest.page_cache, "invalidate_all", _bomb)

    # Run
    async for s in get_session():
        n = await ingest.initial_sync_if_empty(s)
        break

    assert n == 1  # error was swallowed, not propagated


@pytest.mark.asyncio
async def test_refresh_invalidation_failure_is_swallowed(monkeypatch):
    """Refresh: a failure in page_cache.invalidate_all() must not bubble.

    We force a refresh path (no last refresh yet), successful upsert (n=1),
    and then make invalidate_all() raise. The function should still return 1.
    """
    # Force "stale" path: None bypasses freshness short-circuit
    monkeypatch.setattr(ingest, "_last_refresh_ts", None, raising=False)

    # Stub upstream + filtering
    async def _fetch():
        return [
            {
                "id": 2,
                "name": "Rick",
                "status": "Alive",
                "species": "Human",
                "origin": {"name": "Earth (Replacement Dimension)"},
                "image": None,
                "url": "",
            }
        ]

    def _filter(raw):
        return [
            {
                "id": 2,
                "name": "Rick",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (Replacement Dimension)",
                "image": None,
                "url": "",
            }
        ]

    monkeypatch.setattr(api, "fetch_all_characters", _fetch)
    monkeypatch.setattr(api, "filter_character_results", _filter)

    # Upsert succeeds and returns >0
    async def _upsert(_session, _items):
        return 1

    monkeypatch.setattr(crud, "upsert_characters", _upsert)

    # Invalidation throws (exercise the except block)
    def _bomb():
        raise RuntimeError("boom")

    monkeypatch.setattr(ingest.page_cache, "invalidate_all", _bomb)

    # Run
    async for s in get_session():
        n = await ingest.refresh_if_stale(s)
        break

    assert n == 1  # error was swallowed, not propagated


@pytest.mark.asyncio
async def test_characters_ignores_page_cache_failures_and_hits_db(
    monkeypatch, test_app, test_client
):
    def boom(*a, **k):
        raise RuntimeError("cache offline")

    # Patch the alias used by the /characters route
    monkeypatch.setattr(main.page_cache, "get", boom)
    monkeypatch.setattr(main.page_cache, "put", boom)

    async def fake_list(_session, sort, order, page, page_size):
        return ([{"id": 1, "name": "Rick Sanchez"}], 1)

    monkeypatch.setattr(crud, "list_characters", fake_list)

    r = await test_client.get("/characters?sort=id&order=asc&page=1&page_size=10")
    assert r.status_code == 200
    data = r.json()
    assert data["total_count"] == 1
    assert data["results"][0]["id"] == 1

    # Optional: check metrics if you installed them
    m = await test_client.get("/metrics")
    assert m.status_code == 200
    assert (
        'cache_errors_total{cache="page",op="get"}' in m.text
        or 'cache_errors_total{cache="page",op="put"}' in m.text
    )

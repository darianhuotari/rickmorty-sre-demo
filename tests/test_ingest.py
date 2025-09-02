"""Ingest pipeline tests.

Covers:
* Initial seeding if the table is empty.
* TTL-based refresh that no-ops when recently refreshed.
"""

import logging
import pytest

from contextlib import asynccontextmanager
from sqlalchemy import text

from app import db, ingest, crud, api


def _sample_raw():
    """Raw upstream shape: origin is nested under 'origin': {'name': ...}."""
    return [
        {
            "id": 1,
            "name": "Beth Smith",
            "status": "Alive",
            "species": "Human",
            "origin": {"name": "Earth (C-137)"},
            "image": None,
            "url": None,
        },
        {
            "id": 2,
            "name": "Morty Smith",
            "status": "Alive",
            "species": "Human",
            "origin": {"name": "Earth (Replacement Dimension)"},
            "image": None,
            "url": None,
        },
    ]


def _sample_filtered():
    """Filtered local shape: flattened origin, only relevant fields retained."""
    return [
        {
            "id": 1,
            "name": "Beth Smith",
            "status": "Alive",
            "species": "Human",
            "origin": "Earth (C-137)",
            "image": None,
            "url": None,
        },
        {
            "id": 2,
            "name": "Morty Smith",
            "status": "Alive",
            "species": "Human",
            "origin": "Earth (Replacement Dimension)",
            "image": None,
            "url": None,
        },
    ]


@pytest.mark.asyncio
async def test_initial_sync_if_empty_and_noop_when_not_empty(monkeypatch):
    """Seed two rows on first run; subsequent run is a no-op."""
    db.configure_engine("sqlite+aiosqlite:///:memory:")
    await db.init_db()

    async def fake_fetch():
        return _sample_raw()

    def fake_filter(chars):
        return _sample_filtered()

    monkeypatch.setattr(api, "fetch_all_characters", fake_fetch)
    monkeypatch.setattr(api, "filter_character_results", fake_filter)

    async with db.SessionLocal() as s:
        n = await ingest.initial_sync_if_empty(s)
        assert n == 2
        assert await crud.count_characters(s) == 2

        n2 = await ingest.initial_sync_if_empty(s)
        assert n2 == 0


@pytest.mark.asyncio
async def test_refresh_if_stale_behaves_with_ttl(monkeypatch):
    """Refresh when stale then no-op when still fresh."""
    db.configure_engine("sqlite+aiosqlite:///:memory:")
    await db.init_db()

    async def fake_fetch():
        return _sample_raw()

    def fake_filter(chars):
        return _sample_filtered()

    monkeypatch.setattr(api, "fetch_all_characters", fake_fetch)
    monkeypatch.setattr(api, "filter_character_results", fake_filter)

    async with db.SessionLocal() as s:
        await crud.upsert_characters(s, _sample_filtered()[:1])

        ingest._last_refresh_ts = 0
        monkeypatch.setattr(ingest, "REFRESH_TTL", 600, raising=False)

        n1 = await ingest.refresh_if_stale(s)
        assert n1 >= 1
        assert await crud.count_characters(s) == 2

        n2 = await ingest.refresh_if_stale(s)
        assert n2 == 0


class FakeScalar:
    """Result wrapper that returns a specific scalar() value."""

    def __init__(self, value):
        self._v = value

    def scalar(self):
        return self._v


class FakeSession:
    """Minimal AsyncSession stub exposing execute()."""

    def __init__(self, seq):
        """
        seq: iterable of values or exceptions to yield on each execute().
             Values are returned wrapped in FakeScalar; Exceptions are raised.
        """
        self._seq = list(seq)
        self.calls = 0

    async def execute(self, *_a, **_k):
        self.calls += 1
        v = self._seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return FakeScalar(v)


@pytest.mark.asyncio
async def test_initial_sync_no_lock_acquired_is_noop(monkeypatch):
    """When pg_try_advisory_lock returns False, initial_sync should short-circuit."""
    session = FakeSession([False])  # lock NOT acquired

    # Ensure we don't call into CRUD when we don't hold the lock
    called = {"count": 0}

    async def never_called(*a, **k):
        called["count"] += 1
        return 0

    monkeypatch.setattr(crud, "count_characters", never_called)

    n = await ingest.initial_sync_if_empty(session)  # should return 0 early
    assert n == 0
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_initial_sync_lock_fn_raises_proceeds_unlocked(monkeypatch):
    """If SELECT pg_try_advisory_lock raises (non-PG), we proceed (yield True)."""
    session = FakeSession([RuntimeError("no pg fn")])  # triggers except path in lock CM

    # Make table empty so we take the ingest branch
    async def count_zero(_):
        return 0

    monkeypatch.setattr(crud, "count_characters", count_zero)

    async def fake_fetch():
        return [
            {
                "id": 1,
                "name": "A",
                "status": "Alive",
                "species": "Human",
                "origin": {"name": "Earth (C-137)"},
                "image": None,
                "url": None,
            }
        ]

    def fake_filter(chars):
        return [
            {
                "id": 1,
                "name": "A",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (C-137)",
                "image": None,
                "url": None,
            }
        ]

    async def upsert_one(_s, rows):
        return len(rows)

    monkeypatch.setattr(api, "fetch_all_characters", fake_fetch)
    monkeypatch.setattr(api, "filter_character_results", fake_filter)
    monkeypatch.setattr(crud, "upsert_characters", upsert_one)

    # Do the sync; should ingest 1 row and set last_refresh_ts
    ingest._last_refresh_ts = None
    n = await ingest.initial_sync_if_empty(session)
    assert n == 1
    assert ingest.last_refresh_age() is not None


@pytest.mark.asyncio
async def test_refresh_if_stale_lock_not_acquired_is_noop(monkeypatch):
    """When lock is False during refresh, the call should no-op and return 0."""
    session = FakeSession([False])  # lock NOT acquired

    # Force "stale" so we attempt a refresh path
    ingest._last_refresh_ts = 0
    monkeypatch.setenv("REFRESH_TTL", "1", prepend=False)

    n = await ingest.refresh_if_stale(session)
    assert n == 0


@pytest.mark.asyncio
async def test_refresh_if_stale_success_path(monkeypatch):
    """Lock True and stale -> fetch/filter/upsert and update last_refresh."""
    session = FakeSession([True])  # lock acquired

    async def fake_fetch():
        return [
            {
                "id": 2,
                "name": "B",
                "status": "Alive",
                "species": "Human",
                "origin": {"name": "Earth (R)"},
                "image": None,
                "url": None,
            }
        ]

    def fake_filter(chars):
        return [
            {
                "id": 2,
                "name": "B",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (R)",
                "image": None,
                "url": None,
            }
        ]

    async def upsert_one(_s, rows):
        return len(rows)

    monkeypatch.setattr(api, "fetch_all_characters", fake_fetch)
    monkeypatch.setattr(api, "filter_character_results", fake_filter)
    monkeypatch.setattr(crud, "upsert_characters", upsert_one)

    ingest._last_refresh_ts = 0  # force stale
    n = await ingest.refresh_if_stale(session)
    assert n == 1
    # age should be small (recently set); just ensure it's numeric
    assert isinstance(ingest.last_refresh_age(), float)

@pytest.mark.asyncio
async def test_pg_advisory_lock_logs_release_when_supported(caplog, monkeypatch):
    """
    Force the advisory lock *and* unlock paths to 'succeed' so we hit the
    '...released' log line in the finally-block.
    """
    # Fresh in-memory DB
    db.configure_engine("sqlite+aiosqlite:///:memory:")
    await db.init_db()

    class FakeResult:
        def scalar(self):
            return True  # reports lock acquired

    async def fake_execute(_sql, _params=None):
        # Pretend both pg_try_advisory_lock and pg_advisory_unlock succeed
        return FakeResult()

    caplog.set_level(logging.DEBUG, logger="app.ingest")

    async with db.SessionLocal() as s:
        # Patch session.execute so both lock and unlock "work"
        monkeypatch.setattr(s, "execute", fake_execute)

        async with ingest._pg_advisory_lock(s, 0xBEEF) as have:
            assert have is True

    # Should see the 'released' message (covers line ~53)
    assert any("advisory_lock key=" in rec.message and "released" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_initial_sync_logs_skip_when_already_populated(caplog, monkeypatch):
    """Seed using the SAME session, then call initial_sync_if_empty() to hit the skip log."""
    db.configure_engine("sqlite+aiosqlite:///:memory:")
    await db.init_db()

    caplog.set_level(logging.DEBUG, logger="app.ingest")

    # Ensure we always "hold" the advisory lock to reach the count branch.
    @asynccontextmanager
    async def fake_lock(_session, _key):
        yield True
    monkeypatch.setattr(ingest, "_pg_advisory_lock", fake_lock)

    async with db.SessionLocal() as s:
        # Seed one row
        await crud.upsert_characters(
            s,
            [{
                "id": 1, "name": "Beth Smith", "status": "Alive",
                "species": "Human", "origin": "Earth (C-137)",
                "image": None, "url": None
            }],
        )

        # Now call initial_sync in the SAME session/connection
        n = await ingest.initial_sync_if_empty(s)
        assert n == 0  # skipped branch returns 0

    # Should see the 'skipped' debug (covers lines 75–76)
    assert any(
        "initial_sync skipped: table already populated" in rec.message
        for rec in caplog.records
    )
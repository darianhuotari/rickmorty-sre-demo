"""Ingest pipeline tests.

Covers:
* Initial seeding if the table is empty.
* TTL-based refresh that no-ops when recently refreshed.
"""

import pytest
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

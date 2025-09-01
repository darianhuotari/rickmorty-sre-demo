"""Bootstrap and refresh pipeline for local persistence.

Composes the upstream API client (fetch/filter) with CRUD upserts to populate
and periodically refresh the local database.
"""

import os
import time
from contextlib import asynccontextmanager, suppress

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import api, crud

REFRESH_TTL = int(os.getenv("REFRESH_TTL", "600"))  # seconds
_last_refresh_ts: float | None = None


def last_refresh_age() -> float | None:
    """Return seconds since last refresh (rounded) or None if never refreshed."""
    if _last_refresh_ts is None:
        return None
    return round(time.time() - _last_refresh_ts, 2)


@asynccontextmanager
async def _pg_advisory_lock(session: AsyncSession, key: int):
    """Try to acquire a Postgres advisory lock; yield True if held.

    On non-Postgres engines (e.g., SQLite) — or if pg_* functions are unavailable —
    this context manager yields True and performs no locking.
    """
    have = True
    try:
        res = await session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        scalar = res.scalar()
        have = bool(scalar) if scalar is not None else True
    except Exception:
        # Non-PG or no function — proceed unlocked (safe in single-writer tests/dev).
        have = True
    try:
        yield have
    finally:
        if have:
            with suppress(Exception):
                await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})


async def initial_sync_if_empty(session: AsyncSession) -> int:
    """Seed the database if the `characters` table is empty.

    Fetches from the upstream API, applies assignment filters, and upserts the results.

    Args:
        session: Active async SQLAlchemy session.

    Returns:
        Number of items processed (0 if the table was already populated).
    """
    # Only one pod seeds at a time (no thundering herd on cold start).
    SEED_LOCK_KEY = 0xC0FFEE
    async with _pg_advisory_lock(session, SEED_LOCK_KEY) as have_lock:
        if not have_lock:
            return 0
        if await crud.count_characters(session) == 0:
            raw = await api.fetch_all_characters()
            filtered = api.filter_character_results(raw)
            n = await crud.upsert_characters(session, filtered)
            global _last_refresh_ts
            _last_refresh_ts = time.time()
            return n
    return 0


async def refresh_if_stale(session: AsyncSession) -> int:
    """Refresh character data if the last refresh is older than `REFRESH_TTL`.

    Intended for periodic use (e.g., background task or CronJob).

    Args:
        session: Active async SQLAlchemy session.

    Returns:
        Number of items processed when a refresh occurs, or 0 if still fresh.
    """
    global _last_refresh_ts
    now = time.time()
    if _last_refresh_ts is None or (now - _last_refresh_ts) > REFRESH_TTL:
        # Only one pod refreshes at a time
        REFRESH_LOCK_KEY = 0xBEEFED
        async with _pg_advisory_lock(session, REFRESH_LOCK_KEY) as have_lock:
            if not have_lock:
                return 0
            raw = await api.fetch_all_characters()
            filtered = api.filter_character_results(raw)
            n = await crud.upsert_characters(session, filtered)
            _last_refresh_ts = time.time()
            return n
    return 0

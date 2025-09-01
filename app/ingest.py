"""Bootstrap and refresh pipeline for local persistence.

Composes the upstream API client (fetch/filter) with CRUD upserts to populate
and periodically refresh the local database.
"""

import os
import time
from sqlalchemy.ext.asyncio import AsyncSession
from . import api, crud

REFRESH_TTL = int(os.getenv("REFRESH_TTL", "600"))  # seconds
_last_refresh_ts: float | None = None


def last_refresh_age() -> float | None:
    """Return seconds since last refresh (rounded) or None if never refreshed."""
    if _last_refresh_ts is None:
        return None
    return round(time.time() - _last_refresh_ts, 2)


async def initial_sync_if_empty(session: AsyncSession) -> int:
    """Seed the database if the `characters` table is empty.

    Fetches from the upstream API, applies assignment filters, and upserts the results.

    Args:
        session: Active async SQLAlchemy session.

    Returns:
        Number of items processed (0 if the table was already populated).
    """
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

    Intended for periodic use (e.g., admin endpoint, background task, or CronJob).

    Args:
        session: Active async SQLAlchemy session.

    Returns:
        Number of items processed when a refresh occurs, or 0 if still fresh.
    """
    global _last_refresh_ts
    now = time.time()
    if _last_refresh_ts is None or (now - _last_refresh_ts) > REFRESH_TTL:
        raw = await api.fetch_all_characters()
        filtered = api.filter_character_results(raw)
        n = await crud.upsert_characters(session, filtered)
        _last_refresh_ts = time.time()
        return n
    return 0

"""Bootstrap and refresh pipeline for local persistence.

Composes the upstream API client (fetch/filter) with CRUD upserts to populate
and periodically refresh the local database.
"""

import os
import time
import logging
from contextlib import asynccontextmanager, suppress

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import api, crud
from .page_cache import page_cache

log = logging.getLogger(__name__)

REFRESH_TTL = int(os.getenv("REFRESH_TTL", "600"))  # seconds
_last_refresh_ts: float | None = None


def last_refresh_age() -> float | None:
    """Return seconds since last refresh.

    Returns:
        Rounded seconds since the last successful refresh, or ``None`` if no
        refresh has occurred.
    """
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
        log.debug("advisory_lock key=%s acquired=%s", hex(key), have)
    except Exception:
        # Non-PG or no function — proceed unlocked (safe in single-writer tests/dev).
        have = True
        log.debug(
            "advisory_lock key=%s not supported on this engine; proceeding", hex(key)
        )
    try:
        yield have
    finally:
        if have:
            with suppress(Exception):
                await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                log.debug("advisory_lock key=%s released", hex(key))


async def initial_sync_if_empty(session: AsyncSession) -> int:
    """Seed the database if the `characters` table is empty.

    Fetches from the upstream API, applies assignment filters, and upserts the results.

    Args:
        session: Active async SQLAlchemy session.

    Returns:
        Number of items processed (0 if the table was already populated).
    """
    SEED_LOCK_KEY = 0xC0FFEE  # Only one pod seeds at a time
    async with _pg_advisory_lock(session, SEED_LOCK_KEY) as have_lock:
        if not have_lock:
            log.debug("initial_sync skipped: lock held by another instance")
            return 0

        count = await crud.count_characters(session)
        if count > 0:
            log.debug("initial_sync skipped: table already populated (rows=%d)", count)
            return 0

        log.info("initial_sync starting: empty table detected")
        raw = await api.fetch_all_characters()
        filtered = api.filter_character_results(raw)
        n = await crud.upsert_characters(session, filtered)

        # Invalidate per-pod page cache AFTER commit
        if n:
            try:
                page_cache.invalidate_all()
                log.debug("ingest.cache cleared after initial sync (upserted=%d)", n)
            except Exception as exc:
                log.debug("ingest.cache invalidate_failed error=%r", exc)

        global _last_refresh_ts
        _last_refresh_ts = time.time()
        log.info(
            "initial_sync complete: fetched=%d filtered=%d upserted=%d",
            len(raw),
            len(filtered),
            n,
        )
        return n


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
    age = None if _last_refresh_ts is None else round(now - _last_refresh_ts, 2)

    if _last_refresh_ts is not None and (now - _last_refresh_ts) <= REFRESH_TTL:
        log.debug("refresh skipped: still fresh (age=%ss ttl=%ss)", age, REFRESH_TTL)
        return 0

    REFRESH_LOCK_KEY = 0xBEEFED  # Only one pod refreshes at a time
    async with _pg_advisory_lock(session, REFRESH_LOCK_KEY) as have_lock:
        if not have_lock:
            log.debug("refresh skipped: lock held by another instance")
            return 0

        log.info("refresh starting: age=%s ttl=%s", age, REFRESH_TTL)
        raw = await api.fetch_all_characters()
        filtered = api.filter_character_results(raw)
        n = await crud.upsert_characters(session, filtered)

        # Invalidate per-pod page cache AFTER commit (only if data changed)
        if n:
            try:
                page_cache.invalidate_all()
                log.debug("ingest.cache cleared after refresh (upserted=%d)", n)
            except Exception as exc:
                log.debug("ingest.cache invalidate_failed error=%r", exc)
        _last_refresh_ts = time.time()

        log.info(
            "refresh complete: fetched=%d filtered=%d upserted=%d age_before=%s",
            len(raw),
            len(filtered),
            n,
            age,
        )
        return n

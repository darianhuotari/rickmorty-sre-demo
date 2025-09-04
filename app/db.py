"""Database bootstrap: engine/session factories and schema initialization.

This module configures the async SQLAlchemy engine and session factory, exposes a
FastAPI dependency for acquiring `AsyncSession`s, and provides utilities to
initialize the schema and (optionally) wait for DB readiness on cold starts.

Env:
    DATABASE_URL            Async SQLAlchemy URL (e.g., postgresql+asyncpg://...).
                            Defaults to SQLite file DB for dev if not set.

    # Optional Postgres pooling hints (applied only for postgresql URLs)
    DB_POOL_SIZE            e.g., "5"
    DB_MAX_OVERFLOW         e.g., "10"
    DB_POOL_RECYCLE         e.g., "1800"

    # Optional startup wait/retry controls (used by wait_for_db(); opt-in from main)
    DB_WAIT_FOR_DB          "1"/"true" to enable waiting logic (default "0")
    DB_WAIT_MAX_ATTEMPTS    max connection attempts (default 30)
    DB_WAIT_BACKOFF_START   initial backoff seconds (default 0.5)
    DB_WAIT_BACKOFF_MAX     backoff cap seconds (default 5.0)
"""

from __future__ import annotations

import os
import asyncio
import logging
from typing import AsyncIterator

from sqlalchemy import text, event
from sqlalchemy.engine.url import make_url, URL
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool, NullPool

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./app.db")

# Startup wait (you call wait_for_db() from main if you want it)
DB_WAIT_FOR_DB = os.getenv("DB_WAIT_FOR_DB", "0").lower() not in ("0", "false", "no")
DB_WAIT_MAX_ATTEMPTS = int(os.getenv("DB_WAIT_MAX_ATTEMPTS", "30"))
DB_WAIT_BACKOFF_START = float(os.getenv("DB_WAIT_BACKOFF_START", "0.5"))
DB_WAIT_BACKOFF_MAX = float(os.getenv("DB_WAIT_BACKOFF_MAX", "5.0"))


class Base(DeclarativeBase):
    """Declarative base for ORM models."""

    pass


def _safe_url_parts(url_str: str) -> dict:  # NEW
    """Parse an SQLAlchemy URL and return non-sensitive parts for logging."""
    try:
        u: URL = make_url(url_str)
        return {
            "driver": u.drivername or "",
            "host": u.host or "",
            "port": u.port or "",
            "database": u.database or "",
        }
    except Exception:
        return {"driver": "unknown", "host": "", "port": "", "database": ""}


def _register_engine_listeners(eng) -> None:
    """Attach basic connect/dispose logs; safely no-op for dummy engines."""
    try:
        sync_eng = eng.sync_engine  # real AsyncEngine has this
    except Exception:
        # Tests may inject a dummy object without sync_engine
        log.debug("db.listeners skipped: engine has no sync_engine (test/dummy)")
        return

    @event.listens_for(sync_eng, "connect")
    def _on_connect(dbapi_conn, conn_record):
        parts = _safe_url_parts(str(eng.url))
        log.info(
            "db.connect driver=%s host=%s port=%s db=%s",
            parts["driver"],
            parts["host"],
            parts["port"],
            parts["database"],
        )

    @event.listens_for(sync_eng, "engine_disposed")
    def _on_dispose(engine):
        parts = _safe_url_parts(str(eng.url))
        log.info(
            "db.dispose driver=%s host=%s port=%s db=%s",
            parts["driver"],
            parts["host"],
            parts["port"],
            parts["database"],
        )


def _mk_engine(url: str):
    """Build an async engine with sensible defaults by backend."""
    kwargs: dict = {"pool_pre_ping": True}

    if url.startswith("sqlite+aiosqlite:///:memory:"):
        # Single-process in-memory DB for tests
        kwargs["poolclass"] = StaticPool
    elif url.startswith("sqlite+aiosqlite://"):
        # File-backed SQLite: don't hold file descriptors in a pool
        kwargs["poolclass"] = NullPool
    elif url.startswith("postgresql"):
        # Read pool hints at call time so tests can monkeypatch env
        pool_size = os.getenv("DB_POOL_SIZE")
        max_overflow = os.getenv("DB_MAX_OVERFLOW")
        pool_recycle = os.getenv("DB_POOL_RECYCLE")
        if pool_size is not None:
            kwargs["pool_size"] = int(pool_size)
        if max_overflow is not None:
            kwargs["max_overflow"] = int(max_overflow)
        if pool_recycle is not None:
            kwargs["pool_recycle"] = int(pool_recycle)

    eng = create_async_engine(url, **kwargs)

    # Defensive: tests may inject a dummy engine without .sync_engine, or
    # a monkeypatch may intentionally raise. Don’t crash; log and continue.
    try:
        _register_engine_listeners(eng)
    except Exception as exc:
        log.debug("db.listeners registration failed: %r", exc)

    parts = _safe_url_parts(url)
    log.debug(
        "db.engine_created driver=%s host=%s port=%s db=%s kwargs=%s",
        parts["driver"],
        parts["host"],
        parts["port"],
        parts["database"],
        {k: kwargs[k] for k in sorted(kwargs)},
    )

    return eng


# Global engine/session factory (reconfigurable in tests)
engine = _mk_engine(DATABASE_URL)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def configure_engine(url: str) -> None:
    """Reconfigure the global engine/session factory (handy for tests)."""
    global engine, SessionLocal
    engine = _mk_engine(url)
    SessionLocal = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    parts = _safe_url_parts(url)
    log.info(
        "db.engine_reconfigured driver=%s host=%s port=%s db=%s",
        parts["driver"],
        parts["host"],
        parts["port"],
        parts["database"],
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an `AsyncSession`."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all database tables for registered ORM models."""
    from . import models  # noqa: F401 (import registers metadata)

    parts = _safe_url_parts(str(engine.url))
    log.info(
        "db.init begin driver=%s host=%s port=%s db=%s",
        parts["driver"],
        parts["host"],
        parts["port"],
        parts["database"],
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# --------------------------------------------------------------------------------------
# Optional: health ping + wait for DB readiness (call from main if desired)
# --------------------------------------------------------------------------------------


async def ping_db() -> bool:
    """Return True if a simple SELECT succeeds against the current engine."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        log.debug("db.ping failed: %r", e)
        return False


async def wait_for_db(
    *,
    max_attempts: int = DB_WAIT_MAX_ATTEMPTS,
    backoff_start: float = DB_WAIT_BACKOFF_START,
    backoff_max: float = DB_WAIT_BACKOFF_MAX,
) -> None:
    """Poll the database until `ping_db()` returns True or attempts are exhausted."""
    attempt = 0
    delay = backoff_start
    parts = _safe_url_parts(str(engine.url))
    log.info(
        "db.wait start attempts=%d backoff_start=%.3fs backoff_max=%.3fs target=%s@%s:%s/%s",
        max_attempts,
        backoff_start,
        backoff_max,
        parts["driver"],
        parts["host"],
        parts["port"],
        parts["database"],
    )

    while attempt < max_attempts:
        if await ping_db():
            return
        attempt += 1
        if attempt >= max_attempts:
            raise RuntimeError(f"Database not ready after {max_attempts} attempts")
        await asyncio.sleep(delay)
        delay = min(delay * 2.0, backoff_max)

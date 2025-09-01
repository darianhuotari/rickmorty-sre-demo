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
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool, NullPool

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

    return create_async_engine(url, **kwargs)


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


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an `AsyncSession`."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all database tables for registered ORM models."""
    from . import models  # noqa: F401 (import registers metadata)

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
    except Exception:
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
    while attempt < max_attempts:
        if await ping_db():
            return
        attempt += 1
        if attempt >= max_attempts:
            raise RuntimeError(f"Database not ready after {max_attempts} attempts")
        await asyncio.sleep(delay)
        delay = min(delay * 2.0, backoff_max)

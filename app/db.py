"""Database bootstrap: engine/session factories and schema initialization.

This module is the single place that configures the async SQLAlchemy engine and
session factory, exposes a FastAPI dependency for acquiring `AsyncSession`s,
and provides a utility to initialize the schema (create tables).

Env:
    DATABASE_URL: Async SQLAlchemy URL (e.g., postgresql+asyncpg://...).
                  Defaults to SQLite file DB for dev if not set.
"""

import os
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./app.db")


class Base(DeclarativeBase):
    """Declarative base for ORM models.

    All SQLAlchemy ORM models should inherit from this base class.
    """

    pass


# Global engine/session factory with a configurator (handy for tests)
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def configure_engine(url: str):
    """Reconfigure the global engine/session factory.

    Useful in tests to point the ORM to an isolated database (e.g., in-memory SQLite).

    Args:
        url: SQLAlchemy connection URL (async driver).
    """
    global engine, SessionLocal
    engine = create_async_engine(url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an `AsyncSession` for FastAPI dependency injection.

    This ensures each request gets a fresh session that is properly closed
    when the request finishes.

    Yields:
        An `AsyncSession` instance.
    """
    async with SessionLocal() as session:
        yield session


async def init_db():
    """Create all database tables for registered ORM models.

    Invoked at application startup to ensure the schema exists for the current engine.
    """
    from . import models  # noqa: F401  (import registers metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

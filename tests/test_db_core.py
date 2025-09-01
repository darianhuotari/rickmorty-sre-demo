"""DB bootstrap smoke tests.

Ensures the async engine and session factory are correctly configured and that
we can execute a trivial statement using a proper async session context.
"""

import asyncio
import pytest
from sqlalchemy import text
from app import db


@pytest.mark.asyncio
async def test_configure_engine_and_init_db_creates_tables():
    """Create an in-memory engine, init schema, and execute a trivial query."""
    db.configure_engine("sqlite+aiosqlite:///:memory:")
    await db.init_db()
    async with db.SessionLocal() as s:
        await s.execute(text("select 1"))


def _fake_engine_factory(captured):
    """Return a fake create_async_engine that captures kwargs and returns a dummy."""

    def _fake(url, **kwargs):
        captured["url"] = str(url)
        captured["kwargs"] = kwargs
        return object()  # dummy engine

    return _fake


def test_engine_builder_postgres_pool_kwargs(monkeypatch):
    """Postgres URL should pass pool sizing kwargs when env vars are set."""
    cap = {}
    monkeypatch.setattr(db, "create_async_engine", _fake_engine_factory(cap))
    monkeytail = {
        "DB_POOL_SIZE": "5",
        "DB_MAX_OVERFLOW": "10",
        "DB_POOL_RECYCLE": "1234",
    }
    for k, v in monkeytail.items():
        monkeypatch.setenv(k, v, prepend=False)

    db.configure_engine("postgresql+asyncpg://user:pass@host:5432/dbname")

    assert "postgresql+asyncpg://" in cap["url"]
    # pool_pre_ping always set
    assert cap["kwargs"]["pool_pre_ping"] is True
    # from env:
    assert cap["kwargs"]["pool_size"] == 5
    assert cap["kwargs"]["max_overflow"] == 10
    assert cap["kwargs"]["pool_recycle"] == 1234


def test_engine_builder_sqlite_memory_uses_staticpool(monkeypatch):
    """sqlite in-memory should use StaticPool."""
    cap = {}
    monkeypatch.setattr(db, "create_async_engine", _fake_engine_factory(cap))
    db.configure_engine("sqlite+aiosqlite:///:memory:")

    # StaticPool path doesn't include pool_size/overflow
    assert cap["kwargs"]["pool_pre_ping"] is True
    # For StaticPool, key is 'poolclass'
    from sqlalchemy.pool import StaticPool

    assert cap["kwargs"]["poolclass"] is StaticPool


def test_engine_builder_sqlite_file_uses_nullpool(monkeypatch, tmp_path):
    """sqlite file should use NullPool (avoid pooled file handles)."""
    cap = {}
    monkeypatch.setattr(db, "create_async_engine", _fake_engine_factory(cap))
    db.configure_engine(f"sqlite+aiosqlite:///{tmp_path/'app.db'}")

    from sqlalchemy.pool import NullPool

    assert cap["kwargs"]["pool_pre_ping"] is True
    assert cap["kwargs"]["poolclass"] is NullPool


@pytest.mark.asyncio
async def test_ping_db_true_and_false(monkeypatch):
    """ping_db returns True on SELECT 1, False when engine.connect raises."""

    class OKConn:
        async def execute(self, _):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class BadConn:
        async def __aenter__(self):
            raise RuntimeError("nope")

        async def __aexit__(self, *a):
            return False

    class OKEngine:
        def connect(self):
            return OKConn()

    class BadEngine:
        def connect(self):
            return BadConn()

    # True branch
    monkeypatch.setattr(db, "engine", OKEngine())
    assert await db.ping_db() is True

    # False branch (exception)
    monkeypatch.setattr(db, "engine", BadEngine())
    assert await db.ping_db() is False


@pytest.mark.asyncio
async def test_wait_for_db_succeeds_after_retries(monkeypatch):
    """wait_for_db should loop until ping_db returns True."""
    calls = {"n": 0}

    async def fake_ping():
        calls["n"] += 1
        return calls["n"] >= 3  # False, False, then True

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(db, "ping_db", fake_ping)
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    await db.wait_for_db(max_attempts=5, backoff_start=0.01, backoff_max=0.02)
    assert calls["n"] == 3  # looped twice, then succeeded


@pytest.mark.asyncio
async def test_wait_for_db_raises_after_exhaustion(monkeypatch):
    """wait_for_db raises RuntimeError when ping_db never returns True."""

    async def always_false():
        return False

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(db, "ping_db", always_false)
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    with pytest.raises(RuntimeError):
        await db.wait_for_db(max_attempts=2, backoff_start=0.01, backoff_max=0.02)

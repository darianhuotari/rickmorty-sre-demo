"""DB bootstrap smoke tests.

Ensures the async engine and session factory are correctly configured and that
we can execute a trivial statement using a proper async session context.
"""

import asyncio
import logging
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


def test_safe_url_parts_bad_url(caplog):
    """_safe_url_parts should handle invalid URLs gracefully."""
    caplog.set_level(logging.DEBUG)
    parts = db._safe_url_parts("not:a_real_url://%/")
    # It should return fallbacks without raising
    assert parts["driver"] == "unknown"


def test_register_engine_listeners_skips_dummy(caplog):
    """_register_engine_listeners should skip objects without sync_engine."""
    caplog.set_level(logging.DEBUG)

    class Dummy:
        pass

    dummy_engine = Dummy()
    db._register_engine_listeners(dummy_engine)
    # Should log a skip message
    assert "listeners skipped" in caplog.text


def test_mk_engine_listener_registration_failure(monkeypatch, caplog):
    """_mk_engine should catch failures in listener registration."""
    caplog.set_level(logging.DEBUG)

    # Patch _register_engine_listeners to raise
    def boom(_eng):
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "_register_engine_listeners", boom)

    eng = db._mk_engine("sqlite+aiosqlite:///:memory:")
    assert eng is not None
    assert "db.listeners registration failed" in caplog.text


@pytest.mark.asyncio
async def test_init_db_logs_begin(monkeypatch, caplog):
    """
    Cover db.init_db() lines that parse the URL and log the begin message.
    We avoid real DDL by patching Base.metadata.create_all.
    """
    caplog.set_level(logging.INFO)

    # Make create_all a no-op to avoid touching a real DB
    called = {"n": 0}

    def fake_create_all(_):
        called["n"] += 1

    # Patch the run_sync target
    class FakeBaseMeta:
        def create_all(self, conn):  # signature that run_sync expects
            fake_create_all(conn)

    monkeypatch.setattr(db.Base, "metadata", FakeBaseMeta())

    # Run
    await db.init_db()

    # Assert we logged the "db.init begin ..." line (covers the 2 lines)
    assert any("db.init begin" in rec.message for rec in caplog.records), caplog.text
    # And that create_all was invoked
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_engine_dispose_logs(caplog):
    """
    Cover db._register_engine_listeners() 'engine_disposed' path:
    we create a real async engine (sqlite memory), dispose the sync engine,
    and assert the 'db.dispose ...' log is emitted.
    """
    caplog.set_level(logging.INFO)

    eng = db._mk_engine("sqlite+aiosqlite:///:memory:")

    # Disposing the sync engine should trigger our 'engine_disposed' listener.
    eng.sync_engine.dispose()

    assert any("db.dispose" in rec.message for rec in caplog.records), caplog.text

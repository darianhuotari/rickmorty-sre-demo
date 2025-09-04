import logging
import pytest
from app import main
import asyncio as _asyncio


@pytest.mark.asyncio
async def test_lifespan_disables_refresh_worker_when_flag_off(monkeypatch):
    """When REFRESH_WORKER_ENABLED=0, lifespan should not start a background task."""
    # Force enabled=False inside main.lifespan
    real_getenv = main.os.getenv

    def fake_getenv(key, default=None):
        if key == "REFRESH_WORKER_ENABLED":
            return "0"  # forces disabled
        return real_getenv(key, default)

    monkeypatch.setattr(main.os, "getenv", fake_getenv)

    # No-op DB/bootstrap calls
    async def noop(*a, **k):
        return None

    monkeypatch.setattr(main, "wait_for_db", noop)
    monkeypatch.setattr(main, "init_db", noop)

    async def sync_if_empty(_):
        return 0

    monkeypatch.setattr(main.ingest, "initial_sync_if_empty", sync_if_empty)

    # Spy on asyncio.create_task BUT only intercept the refresher;
    # pass through all other tasks (e.g., AsyncSession.close) to avoid warnings.
    real_create_task = _asyncio.create_task
    calls = {"n": 0}

    def create_task_spy(coro, *args, **kwargs):
        # coroutine objects expose cr_code.co_name
        name = getattr(getattr(coro, "cr_code", None), "co_name", None)
        if name == "_refresher":
            calls["n"] += 1

            class DummyTask:
                def cancel(self):
                    pass

            return DummyTask()
        return real_create_task(coro, *args, **kwargs)

    monkeypatch.setattr(main.asyncio, "create_task", create_task_spy)

    # Run lifespan
    async with main.lifespan(main.app):
        pass

    # Assert no background worker was scheduled
    assert (
        calls["n"] == 0
    ), f"Background task should not start when disabled, got {calls['n']} call(s)"


@pytest.mark.asyncio
async def test_healthcheck_logs_and_handles_db_error(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    # Make upstream probe awaitable
    async def ok():
        return True

    monkeypatch.setattr(main.api, "quick_upstream_probe", ok)

    # Fake session
    class FakeSession:
        pass

    fake_session = FakeSession()

    # Force DB error
    async def boom(_session):
        raise RuntimeError("db down")

    monkeypatch.setattr(main.crud, "count_characters", boom)

    out = await main.healthcheck(request=None, session=fake_session)

    joined = "\n".join(rec.message for rec in caplog.records)
    assert "route.healthcheck" in joined
    assert "status=degraded" in joined
    assert "db_ok=False" in joined

    assert out["status"] == "degraded"
    assert out["upstream_ok"] is True
    assert out["db_ok"] is False


@pytest.mark.asyncio
async def test_lifespan_refresh_worker_logs_cycle(monkeypatch, caplog):
    """
    Ensure the background refresher logs 'refresh_worker.cycle upserted=%d'
    when it processes items. We enable the worker, stub the session dep, and
    make refresh_if_stale return a positive number exactly once.
    """
    caplog.set_level(logging.INFO)

    # Make the worker enabled and fast
    real_getenv = main.os.getenv

    def fake_getenv(key, default=None):
        if key == "REFRESH_WORKER_ENABLED":
            return "1"
        if key == "REFRESH_INTERVAL":
            return "0.01"
        return real_getenv(key, default)

    monkeypatch.setattr(main.os, "getenv", fake_getenv)

    # No-op DB/bootstrap so startup is instant
    async def noop(*a, **k):
        return None

    monkeypatch.setattr(main, "wait_for_db", noop)
    monkeypatch.setattr(main, "init_db", noop)

    # Seed step returns 0 to keep logs focused
    async def sync_if_empty(_):
        return 0

    monkeypatch.setattr(main.ingest, "initial_sync_if_empty", sync_if_empty)

    # Replace get_session with a 1-iteration async generator
    async def fake_get_session():
        class Dummy:
            pass

        yield Dummy()

    monkeypatch.setattr(main, "get_session", fake_get_session)

    # When the refresher runs, return a positive upsert count
    ran = _asyncio.Event()

    async def fake_refresh(_s):
        ran.set()
        return 5

    monkeypatch.setattr(main.ingest, "refresh_if_stale", fake_refresh)

    # Run lifespan; wait for one refresh cycle to happen, then exit.
    async with main.lifespan(main.app):
        await ran.wait()  # ensure our refresh ran (and logged)
        await _asyncio.sleep(0)  # yield once so the log record is emitted

    # Assert that the cycle log line was produced (covers the missing line)
    text = "\n".join(rec.message for rec in caplog.records)
    assert "refresh_worker.cycle upserted=5" in text, text

"""Background refresh worker tests and healthcheck 'last_refresh_age'."""

import time
import pytest
from fastapi.testclient import TestClient
import app.main as app_main
from app import ingest


def test_background_refresher_runs_and_stops(monkeypatch):
    """Start real lifespan, shorten interval, and verify the loop fires at least once."""
    # Restore the real lifespan (conftest overrides it to skip background work)
    monkeypatch.setattr(
        app_main.app.router, "lifespan_context", app_main.lifespan, raising=False
    )

    # Make the interval tiny and enable the worker explicitly
    monkeypatch.setenv("REFRESH_WORKER_ENABLED", "1", prepend=False)
    monkeypatch.setenv("REFRESH_INTERVAL", "0.05", prepend=False)

    calls = {"n": 0}

    async def fake_refresh(session):
        calls["n"] += 1
        return 0

    monkeypatch.setattr(ingest, "refresh_if_stale", fake_refresh)

    with TestClient(app_main.app):
        # Give the background task a moment to run
        time.sleep(0.15)

    # After shutting down the TestClient, the task is cancelled cleanly
    assert calls["n"] >= 1


def test_healthcheck_includes_last_refresh_age(monkeypatch):
    """Expose a numeric 'last_refresh_age' when a refresh has occurred."""
    # Pretend we refreshed 42 seconds ago
    ingest._last_refresh_ts = time.time() - 42

    client = TestClient(app_main.app)
    r = client.get("/healthcheck")
    j = r.json()

    assert "last_refresh_age" in j
    assert 40 <= j["last_refresh_age"] <= 45


def test_background_refresher_exception_is_swallowed(monkeypatch):
    """Force refresh_if_stale to raise; the task must keep running (no crash)."""
    # Use the real lifespan so the worker runs (conftest replaces it by default)
    monkeypatch.setattr(
        app_main.app.router, "lifespan_context", app_main.lifespan, raising=False
    )

    # Enable worker and make it tick fast
    monkeypatch.setenv("REFRESH_WORKER_ENABLED", "1", prepend=False)
    monkeypatch.setenv("REFRESH_INTERVAL", "0.05", prepend=False)

    calls = {"n": 0}

    async def boom(session):
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(ingest, "refresh_if_stale", boom)

    # Start/stop the app; give the worker a moment to run (and raise)
    with TestClient(app_main.app):
        time.sleep(0.12)

    # If we got here without exploding, the exception was swallowed by the worker
    assert calls["n"] >= 1


def test_startup_fails_when_wait_for_db_raises(monkeypatch):
    """If wait_for_db fails, startup should fail and propagate the error (cover log+raise)."""

    async def boom():
        raise RuntimeError("db down hard")

    # Ensure we use the real lifespan (some suites override it in conftest)
    monkeypatch.setattr(
        app_main.app.router, "lifespan_context", app_main.lifespan, raising=False
    )
    monkeypatch.setattr(app_main, "wait_for_db", boom)

    with pytest.raises(RuntimeError):
        with TestClient(app_main.app):
            pass  # should never reach here

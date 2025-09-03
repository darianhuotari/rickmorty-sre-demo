"""Lifespan and healthcheck tests.

Covers:
* Startup lifespan sequence (schema init + initial ingest call).
* Deep healthcheck behavior for OK vs degraded states.
"""

from fastapi.testclient import TestClient
import app.main as app_main
from app import api, crud, ingest


def test_lifespan_calls_init_and_initial_sync(monkeypatch):
    """Verify that the app lifespan calls `init_db` and `initial_sync_if_empty`.

    We patch the symbols bound in `app.main` and restore the real lifespan
    (conftest replaces it to skip ingest during most tests) so we can count calls.
    """
    calls = {"init": 0, "seed": 0}

    async def fake_init_db():
        calls["init"] += 1

    async def fake_seed(session):
        calls["seed"] += 1
        return 0

    monkeypatch.setattr(app_main, "init_db", fake_init_db)
    monkeypatch.setattr(ingest, "initial_sync_if_empty", fake_seed)
    monkeypatch.setattr(
        app_main.app.router, "lifespan_context", app_main.lifespan, raising=False
    )

    with TestClient(app_main.app):
        pass

    assert calls["init"] == 1
    assert calls["seed"] == 1


def test_healthcheck_degraded(monkeypatch):
    """Return 'degraded' when upstream probe fails and DB check raises."""

    async def probe_false():
        return False

    async def count_raises(*a, **k):
        raise RuntimeError("db down")

    async def seed_noop(*a, **k):
        return 0

    monkeypatch.setattr(api, "quick_upstream_probe", probe_false)
    monkeypatch.setattr(crud, "count_characters", count_raises)
    monkeypatch.setattr(ingest, "initial_sync_if_empty", seed_noop)

    with TestClient(app_main.app) as client:
        r = client.get("/healthcheck")
        j = r.json()
        assert j["status"] == "degraded"
        assert j["upstream_ok"] is False
        assert j["db_ok"] is False


def test_healthcheck_ok(monkeypatch):
    """Return 'ok' when upstream probe and DB checks are successful."""

    async def probe_true():
        return True

    async def count_two(*a, **k):
        return 2

    monkeypatch.setattr(api, "quick_upstream_probe", probe_true)
    monkeypatch.setattr(crud, "count_characters", count_two)

    with TestClient(app_main.app) as client:
        r = client.get("/healthcheck")
        j = r.json()
        assert j["status"] == "ok"
        assert j["upstream_ok"] is True
        assert j["db_ok"] is True
        assert j["character_count"] == 2


def test_root_redirects_to_docs():
    """GET / should 307-redirect to the docs URL."""
    client = TestClient(app_main.app)

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307

    expected = app_main.app.docs_url or "/docs"
    assert resp.headers.get("location") == expected


def test_docs_page_loads():
    """Follow the redirect and ensure the docs page renders."""
    client = TestClient(app_main.app)
    resp = client.get("/")  # follow_redirects=True by default
    assert resp.status_code == 200
    assert "Swagger UI" in resp.text  # sanity check


def test_healthz_always_ok():
    """The /healthz endpoint should always return 200/ok for k8s probes."""
    client = TestClient(app_main.app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

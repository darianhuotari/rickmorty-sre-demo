"""Integration tests for the Rick & Morty service with mocked upstream.

This suite exercises the full stack (DB, ingestion, API routes, health) while
mocking upstream HTTP requests via `respx` for fast, deterministic runs.

Covered scenarios:
    * Database init and ingestion from mocked upstream
    * Filtering: Alive + Human + origin startswith("Earth")
    * Sorting and pagination on `/characters`
    * Retry/backoff logic on transient upstream errors
    * Background refresh worker updates reported freshness
    * Healthcheck in degraded state when upstream probe fails
"""

from __future__ import annotations

import os
import json
import asyncio
import importlib
import pathlib
import tempfile
from typing import Any, Dict, List, Set

import pytest
import pytest_asyncio
import respx
import httpx
from httpx import Response
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.main import app
from app import db, ingest, api, crud
from app.db import get_session
from app.page_cache import page_cache


# =========================
# Expected sets from fixtures
# =========================

# Only these should survive your filter (Alive + Human + origin startswith "Earth")
ALLOWED_IDS: Set[int] = {1, 2, 3, 4, 5, 8, 9, 11}
# These must be filtered out
BLOCKED_IDS: Set[int] = {6, 7, 10, 12, 13}


# =========================
# Helpers & shared fixtures
# =========================

os.environ["REQUEST_TIMEOUT"] = "1"  # shrink httpx timeout during tests
os.environ["MAX_RETRIES"] = "2"  # keep retries short

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def mock_rickmorty_pagination(respx_mocked, deny_unmocked_requests):
    """Install a pagination-aware mock for the Rick & Morty character endpoint.

    Matches:
        - https://rickandmortyapi.com/api/character
        - https://rickandmortyapi.com/api/character/
        - ...?page=1
        - ...?page=2
    and lets you provide page1/page2 payloads per-test.

    Usage:
        def test_x(mock_rickmorty_pagination, rickmorty_page1, rickmorty_page2):
            mock_rickmorty_pagination(page1=rickmorty_page1, page2=rickmorty_page2)
            # run code that fetches pages...

    Args:
        respx_mocked: respx router.
        deny_unmocked_requests: catch-all that raises on unmocked calls.

    Returns:
        A function(page1: dict, page2: dict, add_deny_all: bool = True) -> None
        that installs the route.
    """

    def _install(*, page1: dict, page2: dict, add_deny_all: bool = True):
        # 1) Register specific matcher FIRST
        def _page_router(request):
            page = request.url.params.get("page")
            if page in (None, "", "1"):
                return Response(200, json=page1)
            if str(page) == "2":
                return Response(200, json=page2)
            return Response(404, json={"error": f"unexpected page={page}"})

        respx_mocked.get(
            url__regex=r"^https://rickandmortyapi\.com/api/character(?:/)?(?:\?.*)?$"
        ).mock(side_effect=_page_router)

        # 2) THEN add a catch-all to fail fast on anything else
        if add_deny_all:
            respx_mocked.route().mock(
                side_effect=lambda req: (_ for _ in ()).throw(
                    AssertionError(f"UNMOCKED REQUEST: {req.method} {req.url}")
                )
            )

    return _install


@pytest.fixture
def deny_unmocked_requests(respx_mocked):
    """Fail fast on ANY outbound HTTP that wasn't explicitly mocked.

    Args:
        respx_mocked: respx router fixture.

    Returns:
        A function that, when called, installs the deny-all route.
    """

    def _deny():
        respx_mocked.route().mock(
            side_effect=lambda req: (_ for _ in ()).throw(
                AssertionError(f"UNMOCKED REQUEST: {req.method} {req.url}")
            )
        )

    return _deny


def _load_fixture(name: str) -> Dict[str, Any]:
    """Load a JSON fixture from tests/fixtures.

    Args:
        name: File name inside tests/fixtures (e.g., "rickmorty_page1.json").

    Returns:
        Parsed JSON as a Python dict.

    Raises:
        FileNotFoundError: If the fixture file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def rickmorty_page1() -> Dict[str, Any]:
    """First page of mocked upstream results.

    Returns:
        Dict containing `info` and `results` keys as returned by the API.
    """
    return _load_fixture("rickmorty_page1.json")


@pytest.fixture
def rickmorty_page2() -> Dict[str, Any]:
    """Second page of mocked upstream results.

    Returns:
        Dict containing `info` and `results` keys as returned by the API.
    """
    return _load_fixture("rickmorty_page2.json")


@pytest.fixture
def respx_mocked():
    """respx router for mocking httpx requests.

    Yields:
        A respx router that intercepts `httpx` calls.
    """
    with respx.mock(assert_all_called=False) as router:
        yield router


# Clear in-process page cache before/after each test in this module
@pytest.fixture(autouse=True)
def _clear_page_cache_between_tests():
    """Ensure the route-level page cache is clean around every test."""
    page_cache.invalidate_all()
    yield
    page_cache.invalidate_all()


# =========================
# App/DB fixtures
# =========================


@pytest_asyncio.fixture
async def sqlite_db():
    """Create a temporary file-backed SQLite database URL.

    Yields:
        SQLAlchemy async database URL pointing at a temp SQLite file.

    Notes:
        On Windows, the temporary file may be left behind if SQLite holds
        a handle; this fixture tolerates `PermissionError` on unlink.
    """
    db_file = tempfile.NamedTemporaryFile(delete=False)
    db_url = f"sqlite+aiosqlite:///{db_file.name}"

    engine = create_async_engine(db_url)
    db.engine = engine

    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)

    try:
        yield db_url
    finally:
        await engine.dispose()
        db.engine = None
        try:
            os.unlink(db_file.name)
        except PermissionError:
            pass


@pytest_asyncio.fixture
async def test_app(sqlite_db):
    """Configure the FastAPI application to use the test database.

    Args:
        sqlite_db: The temporary SQLite URL fixture.

    Returns:
        FastAPI app instance configured for tests.
    """
    db.configure_engine(sqlite_db)
    app.dependency_overrides = {}
    return app


@pytest_asyncio.fixture
async def test_client(test_app):
    """Create a synchronous TestClient bound to the test app.

    Args:
        test_app: The configured FastAPI application.

    Yields:
        A FastAPI TestClient for making HTTP requests.
    """
    with TestClient(test_app) as client:
        yield client


# =========================
# Utility
# =========================


def _ids(payload: Dict[str, Any]) -> List[int]:
    """Extract character IDs from a `/characters` API response payload.

    Args:
        payload: JSON-decoded response body.

    Returns:
        List of integer character IDs in `results`.
    """
    return [c["id"] for c in payload.get("results", [])]


async def _seed(n: int = 12) -> List[Dict[str, Any]]:
    """Seed the database with `n` basic characters.

    Args:
        n: Number of rows to insert.

    Returns:
        The list of inserted row dicts.
    """
    items: List[Dict[str, Any]] = []
    for i in range(1, n + 1):
        items.append(
            {
                "id": i,
                "name": f"Char{i:03d}",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (C-137)",
                "image": None,
                "url": f"https://example.test/{i}",
            }
        )
    async for s in get_session():
        await crud.upsert_characters(s, items)
        break
    return items


# =========================
# Tests
# =========================


@pytest.mark.asyncio
async def test_full_integration_flow(
    test_app,
    test_client,
    mock_rickmorty_pagination,
    rickmorty_page1,
    rickmorty_page2,
):
    """Test ingestion, filtering, persistence, and query paths using mocked upstream.

    Steps:
        1) Assert initial empty state
        2) Ingest from mocked upstream (two pages)
        3) Assert data exists and equals the ALLOWED set exactly
        4) Assert sorting works by name (desc) over the filtered set

    Args:
        test_app: Configured FastAPI application.
        test_client: Test client bound to the app.
        mock_rickmorty_pagination: respx router for mocking upstream.
        rickmorty_page1: JSON for first page.
        rickmorty_page2: JSON for second page.
    """
    # Mock first and second page
    mock_rickmorty_pagination(page1=rickmorty_page1, page2=rickmorty_page2)
    # 1) Empty state
    r = test_client.get("/characters")
    assert r.status_code == 200
    assert r.json()["total_count"] == 0

    # 2) Ingest
    async for s in get_session():
        await ingest.initial_sync_if_empty(s)
        break

    # 3) Fetch all filtered characters (use large page_size to avoid pagination)
    r = test_client.get("/characters?page=1&page_size=100")
    assert r.status_code == 200
    body = r.json()

    returned_ids = set(_ids(body))
    assert body["total_count"] == len(ALLOWED_IDS)
    assert (
        returned_ids == ALLOWED_IDS
    ), f"Unexpected IDs returned: {returned_ids ^ ALLOWED_IDS}"

    # Ensure none of the blocked IDs slipped through
    assert not (returned_ids & BLOCKED_IDS)

    # 4) Sorting (by name desc) over the filtered set
    r = test_client.get("/characters?sort=name&order=desc&page=1&page_size=100")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()["results"]]
    assert names == sorted(names, reverse=True)


@pytest.mark.asyncio
async def test_retry_logic_and_eventual_success(test_app, respx_mocked):
    """Exercise retry/backoff: two HTTP 500s followed by success.

    This validates that your client retries transient errors and eventually
    succeeds without surfacing a 503.

    Args:
        test_app: Configured FastAPI application.
        respx_mocked: respx router for mocking upstream.
    """
    calls = {"n": 0}

    def flaky(_request):
        calls["n"] += 1
        if calls["n"] < 3:
            return Response(500, json={"error": "upstream oops"})
        # Empty-but-successful page; no results survive filtering
        return Response(
            200,
            json={
                "info": {"count": 0, "pages": 0, "next": None, "prev": None},
                "results": [],
            },
        )

    respx_mocked.get(api.BASE_URL).mock(side_effect=flaky)

    async for s in get_session():
        await ingest.initial_sync_if_empty(s)
        break

    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_background_refresh_updates_age(
    test_app,
    test_client,
    respx_mocked,
    rickmorty_page1,
):
    """Verify background refresh worker updates freshness in /healthcheck.

    The REFRESH_WORKER runs on a short interval and fetches from the mocked
    upstream; we assert the reported `last_refresh_age` changes across a cycle.

    Args:
        test_app: Configured FastAPI application.
        test_client: Test client bound to the app.
        respx_mocked: respx router for mocking upstream.
        rickmorty_page1: JSON for a single page response.
    """
    os.environ["REFRESH_INTERVAL"] = "1"
    os.environ["REFRESH_WORKER_ENABLED"] = "1"

    respx_mocked.get(api.BASE_URL).mock(
        return_value=Response(200, json=rickmorty_page1)
    )

    async with test_app.router.lifespan_context(test_app):
        r1 = test_client.get("/healthcheck")
        assert r1.status_code == 200
        first = r1.json()["last_refresh_age"]

        await asyncio.sleep(2)

        r2 = test_client.get("/healthcheck")
        assert r2.status_code == 200
        second = r2.json()["last_refresh_age"]

        # If never refreshed, values might be equal/None; we expect change
        assert first != second


@pytest.mark.asyncio
async def test_health_check_degraded_state(test_app, test_client, monkeypatch):
    """Report degraded health when upstream probe fails.

    Mocks `api.quick_upstream_probe` to return False, then asserts
    `/healthcheck` reports `status: degraded` and `upstream_ok: False`.

    Args:
        test_app: Configured FastAPI application.
        test_client: Test client bound to the app.
        monkeypatch: Pytest monkeypatch fixture.
    """

    async def _probe_false():
        return False

    monkeypatch.setattr(api, "quick_upstream_probe", _probe_false)

    r = test_client.get("/healthcheck")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "degraded"
    assert payload["upstream_ok"] is False


@pytest.mark.asyncio
async def test_pagination_integration(
    test_app, test_client, mock_rickmorty_pagination, rickmorty_page1, rickmorty_page2
):
    """Validate API pagination returns only filtered records and distinct pages.

    Ingests two mocked pages, then fetches page 1 and page 2 from the service
    and verifies:
      * Only ALLOWED IDs are returned
      * No BLOCKED IDs appear
      * Union across pages equals ALLOWED
      * No overlap between pages

    Args:
        test_app: Configured FastAPI application.
        test_client: Test client bound to the app.
        mock_rickmorty_pagination: respx router for mocking upstream.
        rickmorty_page1: JSON for first page.
        rickmorty_page2: JSON for second page.
    """
    # Mock first and second page
    mock_rickmorty_pagination(page1=rickmorty_page1, page2=rickmorty_page2)

    # Ingest mocked data
    async for s in get_session():
        await ingest.initial_sync_if_empty(s)
        break

    # Use a page_size smaller than ALLOWED size to force pagination
    r1 = test_client.get("/characters?page=1&page_size=5")
    r2 = test_client.get("/characters?page=2&page_size=5")
    assert r1.status_code == 200 and r2.status_code == 200

    page1_ids = set(_ids(r1.json()))
    page2_ids = set(_ids(r2.json()))

    # Only allowed IDs should be present
    assert page1_ids <= ALLOWED_IDS
    assert page2_ids <= ALLOWED_IDS
    assert not (page1_ids & BLOCKED_IDS)
    assert not (page2_ids & BLOCKED_IDS)

    # Distinct pages; combined equals ALLOWED (assuming your API’s default order is stable)
    assert not page1_ids.intersection(page2_ids)
    combined = page1_ids | page2_ids
    # Some implementations may return fewer than `page_size` on final page; ensure at least matches
    assert combined == ALLOWED_IDS, f"Expected {ALLOWED_IDS}, got {combined}"


# =========================
# Additional tests
# =========================


@pytest.mark.asyncio
async def test_bad_input_handling_returns_400(test_app, test_client):
    """Ensure invalid query parameters are rejected with HTTP 400 and clear body.

    Scenarios:
        * Invalid sort field
        * Negative page
        * Zero / negative page_size

    Args:
        test_app: Configured FastAPI application.
        test_client: Test client bound to the app.
    """
    # Invalid sort field
    r = test_client.get("/characters?sort=not_a_field")
    assert r.status_code in (400, 422)
    msg = r.json()
    assert "error" in msg or "detail" in msg

    # Negative page
    r = test_client.get("/characters?page=-1")
    assert r.status_code in (400, 422)
    msg = r.json()
    assert "error" in msg or "detail" in msg

    # Zero / negative page_size
    r = test_client.get("/characters?page_size=0")
    assert r.status_code in (400, 422)
    msg = r.json()
    assert "error" in msg or "detail" in msg


@pytest.mark.asyncio
async def test_idempotent_upserts_no_duplicate_rows(
    test_app,
    test_client,
    mock_rickmorty_pagination,
    rickmorty_page1,
    rickmorty_page2,
):
    """Calling initial_sync_if_empty twice should not duplicate or change row counts.

    Steps:
        1) Ingest using mocked pages.
        2) Record `total_count` from API.
        3) Call initial_sync_if_empty again.
        4) Verify `total_count` unchanged.

    Args:
        test_app: Configured FastAPI application.
        test_client: Test client bound to the app.
        mock_rickmorty_pagination: Pagination-aware upstream mock installer.
        rickmorty_page1: First page fixture.
        rickmorty_page2: Second page fixture.
    """
    mock_rickmorty_pagination(page1=rickmorty_page1, page2=rickmorty_page2)

    async for s in get_session():
        await ingest.initial_sync_if_empty(s)
        break

    r = test_client.get("/characters?page=1&page_size=100")
    assert r.status_code == 200
    first_count = r.json()["total_count"]

    # Second run should be a no-op for counts
    async for s in get_session():
        await ingest.initial_sync_if_empty(s)
        break

    r = test_client.get("/characters?page=1&page_size=100")
    assert r.status_code == 200
    second_count = r.json()["total_count"]

    assert second_count == first_count


@pytest.mark.asyncio
async def test_healthcheck_db_down_reports_degraded(test_app, test_client, monkeypatch):
    """When DB is unavailable, healthcheck should report degraded and flag DB issues.

    We simulate a DB outage by making the session factory yield a broken session.

    Args:
        test_app: Configured FastAPI application.
        test_client: FastAPI TestClient bound to the app.
        monkeypatch: Pytest monkeypatch.
    """

    class BrokenSession:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

        async def scalar(self, *a, **k):
            raise RuntimeError("db down")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    # Override the dependency to yield a BrokenSession
    async def _broken_dependency():
        async with BrokenSession() as s:
            yield s

    from app.main import get_session as _get_session

    app.dependency_overrides[_get_session] = _broken_dependency

    r = test_client.get("/healthcheck")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "degraded"
    if "db_ok" in payload:
        assert payload["db_ok"] is False

    app.dependency_overrides.pop(_get_session, None)


@pytest.mark.asyncio
async def test_healthcheck_stale_but_serving(test_app, test_client, monkeypatch):
    """Upstream down but data is stale-not-fresh should surface 'degraded' and/or 'stale'.

    We:
        * Force last_refresh_ts to appear older than REFRESH_TTL
        * Make upstream probe return False
        * Expect healthcheck to indicate degraded and (if exposed) stale=True

    Args:
        test_app: Configured FastAPI application.
        test_client: FastAPI TestClient bound to the app.
        monkeypatch: Pytest monkeypatch.
    """
    import time

    # Small TTL so we can mark stale easily
    monkeypatch.setenv("REFRESH_TTL", "1")
    # Force "last refresh" to long ago
    monkeypatch.setattr(ingest, "_last_refresh_ts", time.time() - 10, raising=False)

    async def _probe_false():
        return False

    monkeypatch.setattr(api, "quick_upstream_probe", _probe_false)

    r = test_client.get("/healthcheck")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "degraded"
    # Optional flag if you expose it
    if "stale" in payload:
        assert payload["stale"] is True


@pytest.mark.asyncio
async def test_pagination_page_past_end_returns_empty(
    test_app,
    test_client,
    mock_rickmorty_pagination,
    rickmorty_page1,
    rickmorty_page2,
):
    """Requesting a page beyond the available items returns empty results.

    Steps:
        1) Ingest two mocked pages.
        2) Request a very large page number.
        3) Response should have empty `results` and the correct `total_count`.

    Args:
        test_app: Configured FastAPI application.
        test_client: FastAPI TestClient bound to the app.
        mock_rickmorty_pagination: Pagination-aware upstream mock installer.
        rickmorty_page1: First page fixture.
        rickmorty_page2: Second page fixture.
    """
    mock_rickmorty_pagination(page1=rickmorty_page1, page2=rickmorty_page2)

    async for s in get_session():
        await ingest.initial_sync_if_empty(s)
        break

    # Very large page index
    r = test_client.get("/characters?page=999&page_size=5")
    assert r.status_code == 200
    body = r.json()

    assert body["results"] == []
    assert body["total_count"] == len(ALLOWED_IDS)


@pytest.mark.asyncio
async def test_refresh_worker_disabled_no_effect(test_app, test_client, monkeypatch):
    """With background refresh disabled, last_refresh_ts should not change.

    We don't assert that 'age' is constant (time passes), only that the refresh
    timestamp isn't advanced by a background cycle.
    """
    monkeypatch.setenv("REFRESH_WORKER_ENABLED", "0")

    async with test_app.router.lifespan_context(test_app):
        ts0 = ingest._last_refresh_ts  # may be set by initial_sync_if_empty
        r1 = test_client.get("/healthcheck")
        body1 = r1.json()
        r2 = test_client.get("/healthcheck")
        body2 = r2.json()

    # If we've never refreshed/seeded, both ages should be None
    if ts0 is None:
        assert body1["last_refresh_age"] is None
        assert body2["last_refresh_age"] is None
    else:
        # No background refresh -> ts remains unchanged and age is monotonic
        assert ingest._last_refresh_ts == ts0
        assert body2["last_refresh_age"] >= body1["last_refresh_age"]


# =========================
# In-process page cache (route-level) integration tests
# =========================


@pytest.mark.asyncio
async def test_page_cache_hit_avoids_db_call(test_app, test_client, monkeypatch):
    """Second identical request should be served from the route cache (no DB call).

    Steps:
        1) Seed DB; instrument crud.list_characters to count calls.
        2) First GET -> DB called once, response cached.
        3) Second GET (same params) -> cache hit, DB not called again.
    """
    await _seed(n=15)

    # Spy on crud.list_characters (call-through)
    orig = crud.list_characters
    calls = {"n": 0}

    async def _spy(session, sort, order, page, page_size):
        calls["n"] += 1
        return await orig(session, sort, order, page, page_size)

    monkeypatch.setattr(crud, "list_characters", _spy)

    # 1st request -> miss -> DB
    r1 = test_client.get("/characters?sort=id&order=asc&page=1&page_size=5")
    assert r1.status_code == 200
    assert calls["n"] == 1

    # 2nd request (identical) -> cache hit -> no DB
    r2 = test_client.get("/characters?sort=id&order=asc&page=1&page_size=5")
    assert r2.status_code == 200
    assert calls["n"] == 1
    assert r1.json() == r2.json()


@pytest.mark.asyncio
async def test_page_cache_ttl_expiry_triggers_refetch(
    test_app, test_client, monkeypatch
):
    """After TTL expiration, request should miss the cache and hit DB again.

    Uses a patched clock to avoid sleeping.
    """
    await _seed(n=10)

    # Small TTL
    monkeypatch.setattr(page_cache, "_ttl", 1.0, raising=False)

    # Controlled clock for page_cache
    import app.page_cache as mod

    now = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "time", lambda: now["t"])

    # Spy on crud.list_characters
    orig = crud.list_characters
    calls = {"n": 0}

    async def _spy(session, sort, order, page, page_size):
        calls["n"] += 1
        return await orig(session, sort, order, page, page_size)

    monkeypatch.setattr(crud, "list_characters", _spy)

    # First request -> fills cache
    r1 = test_client.get("/characters?page=1&page_size=5")
    assert r1.status_code == 200
    assert calls["n"] == 1

    # Within TTL -> cache hit
    r2 = test_client.get("/characters?page=1&page_size=5")
    assert r2.status_code == 200
    assert calls["n"] == 1

    # Advance beyond TTL -> miss -> DB called again
    now["t"] += 2.0
    r3 = test_client.get("/characters?page=1&page_size=5")
    assert r3.status_code == 200
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_page_cache_invalidation_on_refresh(test_app, test_client, monkeypatch):
    """Successful refresh should invalidate the route page cache.

    Steps:
        1) Seed DB & warm the cache for page=1.
        2) Run ingest.refresh_if_stale() so invalidation happens.
        3) Next request for the same page should hit DB again (miss).
    """
    await _seed(n=9)

    # Ensure refresh happens (treat as stale) and avoid network by stubbing api.*
    monkeypatch.setattr(ingest, "_last_refresh_ts", None, raising=False)

    async def _fake_fetch():
        return [
            {
                "id": 999,
                "name": "NewGuy",
                "status": "Alive",
                "species": "Human",
                "origin": {"name": "Earth (Replacement Dimension)"},
                "image": None,
                "url": "",
            }
        ]

    def _fake_filter(raw):
        return [
            {
                "id": 999,
                "name": "NewGuy",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (Replacement Dimension)",
                "image": None,
                "url": "",
            }
        ]

    monkeypatch.setattr(api, "fetch_all_characters", _fake_fetch)
    monkeypatch.setattr(api, "filter_character_results", _fake_filter)

    # Spy crud.list_characters to observe misses
    orig = crud.list_characters
    calls = {"n": 0}

    async def _spy(session, sort, order, page, page_size):
        calls["n"] += 1
        return await orig(session, sort, order, page, page_size)

    monkeypatch.setattr(crud, "list_characters", _spy)

    # Warm cache
    r1 = test_client.get("/characters?page=1&page_size=5")
    assert r1.status_code == 200
    assert calls["n"] == 1

    # Trigger refresh (upsert returns >0 so invalidate_all() runs)
    async for s in get_session():
        n = await ingest.refresh_if_stale(s)
        break
    assert n > 0  # ensure invalidation path executed

    # Same request after refresh -> miss -> DB called again
    r2 = test_client.get("/characters?page=1&page_size=5")
    assert r2.status_code == 200
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_page_cache_singleflight_under_concurrency(test_app, monkeypatch):
    """Concurrent identical requests should result in exactly one DB call.

    Drives concurrency via httpx.AsyncClient + ASGITransport.
    """
    await _seed(n=20)

    # Slow wrapper around crud.list_characters to expose contention
    orig = crud.list_characters
    calls = {"n": 0}

    async def _slow(session, sort, order, page, page_size):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return await orig(session, sort, order, page, page_size)

    monkeypatch.setattr(crud, "list_characters", _slow)

    # Use ASGITransport (no lifespan kw for this httpx version)
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        responses = await asyncio.gather(
            *[ac.get("/characters?page=1&page_size=5") for _ in range(10)]
        )

    assert {r.status_code for r in responses} == {200}
    bodies = [r.json() for r in responses]
    assert all(b == bodies[0] for b in bodies)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cache_keying_by_params_separates_entries(
    test_app, test_client, monkeypatch
):
    """Different query params should create distinct cache entries."""
    await _seed(n=30)

    orig = crud.list_characters
    calls = {"n": 0}

    async def _spy(session, sort, order, page, page_size):
        calls["n"] += 1
        return await orig(session, sort, order, page, page_size)

    monkeypatch.setattr(crud, "list_characters", _spy)

    # Misses for three distinct keys
    assert test_client.get("/characters?page=1&page_size=5").status_code == 200
    assert test_client.get("/characters?page=2&page_size=5").status_code == 200
    assert (
        test_client.get(
            "/characters?sort=name&order=desc&page=1&page_size=5"
        ).status_code
        == 200
    )
    assert calls["n"] == 3

    # Hits for the same three keys
    assert test_client.get("/characters?page=1&page_size=5").status_code == 200
    assert test_client.get("/characters?page=2&page_size=5").status_code == 200
    assert (
        test_client.get(
            "/characters?sort=name&order=desc&page=1&page_size=5"
        ).status_code
        == 200
    )
    assert calls["n"] == 3  # still 3 -> served from cache


@pytest.mark.asyncio
async def test_out_of_range_pages_are_cached_too(test_app, test_client, monkeypatch):
    """Out-of-range responses should also be cached (for a short TTL).

    This avoids repeated DB hits for obviously invalid navigation.
    """
    # Seed 6 rows; with page_size=5, total_pages=2. Request page=999.
    await _seed(n=6)

    # Long TTL to avoid expiry during test
    monkeypatch.setattr(page_cache, "_ttl", 60.0, raising=False)

    orig = crud.list_characters
    calls = {"n": 0}

    async def _spy(session, sort, order, page, page_size):
        calls["n"] += 1
        return await orig(session, sort, order, page, page_size)

    monkeypatch.setattr(crud, "list_characters", _spy)

    r1 = test_client.get("/characters?page=999&page_size=5")
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1.get("out_of_range") is True
    assert body1["results"] == []
    assert calls["n"] == 1

    # Repeat the same out-of-range request -> serve from cache, no DB call
    r2 = test_client.get("/characters?page=999&page_size=5")
    assert r2.status_code == 200
    assert r2.json() == body1
    assert calls["n"] == 1


@pytest.fixture
def rebind_page_cache_to_current_loop(monkeypatch):
    """Reload app.page_cache so its asyncio.Lock is created on THIS test's loop,
    and rebind the singleton in modules that imported it."""
    import app.page_cache as pc_mod
    import app.main as main_mod
    import app.ingest as ingest_mod

    pc_mod = importlib.reload(pc_mod)  # new singleton; new lock on current loop
    monkeypatch.setattr(main_mod, "page_cache", pc_mod.page_cache, raising=False)
    monkeypatch.setattr(ingest_mod, "page_cache", pc_mod.page_cache, raising=False)

    # If other modules import page_cache directly, rebind them here too.
    return pc_mod.page_cache


@pytest.mark.asyncio
async def test_concurrent_queries_during_refresh_overlap_deterministic_async(
    test_app,
    monkeypatch,
    rebind_page_cache_to_current_loop,
):
    """Deterministically overlap a running refresh with concurrent reads.

    Rebinds the page cache lock to THIS event loop to avoid 'different event loop' errors.
    """

    # --- Rebind the page cache lock to this loop and clear state ---
    from app.page_cache import page_cache as _pc

    # If your singleton exposes a lock attribute, rebind it; also clear cache
    new_lock = asyncio.Lock()
    if hasattr(_pc, "_lock"):
        monkeypatch.setattr(_pc, "_lock", new_lock, raising=False)
    elif hasattr(_pc, "lock"):
        monkeypatch.setattr(_pc, "lock", new_lock, raising=False)
    # Optional: clear cache contents between tests if your API exposes it
    if hasattr(_pc, "invalidate_all"):
        _pc.invalidate_all()

    # --- Seed some rows so reads have data regardless of refresh state ---
    from app import crud, ingest, api
    from app.db import get_session

    async for s in get_session():
        rows = [
            {
                "id": 101,
                "name": "A",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (C-137)",
                "image": None,
                "url": "",
            },
            {
                "id": 102,
                "name": "B",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (Replacement Dimension)",
                "image": None,
                "url": "",
            },
            {
                "id": 103,
                "name": "C",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (C-137)",
                "image": None,
                "url": "",
            },
        ]
        await crud.upsert_characters(s, rows)
        break

    # --- Coordination primitives for deterministic overlap ---
    started = asyncio.Event()
    release = asyncio.Event()

    # Patch upsert to block during refresh so we guarantee overlap
    orig_upsert = crud.upsert_characters

    async def _blocking_upsert(session, items):
        started.set()  # signal refresh reached the write phase
        await release.wait()  # hold until we've fired the concurrent reads
        return await orig_upsert(session, items)

    monkeypatch.setattr(crud, "upsert_characters", _blocking_upsert)

    # Stub upstream fetch/filter to avoid real HTTP
    async def _fake_fetch():
        return [
            {
                "id": 9001,
                "name": "New A",
                "status": "Alive",
                "species": "Human",
                "origin": {"name": "Earth (C-137)"},
                "image": None,
                "url": "",
            },
            {
                "id": 9002,
                "name": "New B",
                "status": "Alive",
                "species": "Human",
                "origin": {"name": "Earth (Replacement Dimension)"},
                "image": None,
                "url": "",
            },
        ]

    def _fake_filter(raw):
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "status": r["status"],
                "species": r["species"],
                "origin": r["origin"]["name"],
                "image": r["image"],
                "url": r["url"],
            }
            for r in raw
        ]

    monkeypatch.setattr(api, "fetch_all_characters", _fake_fetch)
    monkeypatch.setattr(api, "filter_character_results", _fake_filter)

    # Make data "stale" so a refresh will run
    monkeypatch.setattr(ingest, "REFRESH_TTL", 0, raising=False)
    monkeypatch.setattr(ingest, "_last_refresh_ts", 0.0, raising=False)

    # Kick off a one-shot refresh in the background (no reliance on app's worker)
    async def _run_refresh_once():
        async for s in get_session():
            await ingest.refresh_if_stale(s)
            break

    refresh_task = asyncio.create_task(_run_refresh_once())

    # Wait until refresh has entered our blocking upsert
    await asyncio.wait_for(started.wait(), timeout=5.0)

    # While refresh is in-flight, issue many concurrent /characters requests (same loop)
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        responses = await asyncio.gather(
            *[ac.get("/characters?page_size=5") for _ in range(16)]
        )

    # All reads should succeed and not deadlock
    assert {r.status_code for r in responses} == {200}
    for r in responses:
        body = r.json()
        assert isinstance(body.get("total_count"), int)
        assert body["total_count"] >= 0

    # Let the refresh complete and ensure the task finishes
    release.set()
    await asyncio.wait_for(refresh_task, timeout=5.0)

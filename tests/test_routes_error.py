"""HTTP error path tests.

Ensures /characters surfaces a 400 as RFC7807 problem+json when the CRUD layer
signals an invalid query.
"""

import asyncio
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import crud
import app.main as main


def test_characters_route_400_when_name_not_string(monkeypatch):
    """Surface HTTP 400 with a clear problem+json body on bad query/sort."""

    async def bad_list(*a, **k):
        raise ValueError("bad sort")

    monkeypatch.setattr(crud, "list_characters", bad_list)

    client = TestClient(app)
    resp = client.get("/characters?sort=name&order=asc")

    assert resp.status_code == 400
    assert resp.headers.get("content-type", "").startswith("application/problem+json")

    body = resp.json()
    assert body["status"] == 400
    assert body["title"] == "Bad Request"
    assert body["detail"] == "Invalid sort parameter or query"


@pytest.mark.asyncio
async def test_characters_503_on_db_timeout(monkeypatch, test_app, test_client):
    main.page_cache.invalidate_all()  # avoid cache enabling a 200 response

    async def boom(*a, **k):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(crud, "list_characters", boom)
    r = await test_client.get("/characters?sort=id&order=asc&page=1&page_size=10")
    assert r.status_code == 503
    assert r.json()["detail"]


@pytest.mark.asyncio
async def test_characters_500_on_unexpected_error(monkeypatch, test_app, test_client):
    main.page_cache.invalidate_all()  # avoid cache enabling a 200 response

    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(crud, "list_characters", boom)
    r = await test_client.get("/characters?sort=id&order=asc&page=1&page_size=10")
    assert r.status_code == 500

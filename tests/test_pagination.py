"""Pagination behavior tests.

Covers first/middle/last page calculations and behavior beyond the last page.
"""

from fastapi.testclient import TestClient
from app.main import app
from app import crud


def _fake_list(n=50):
    """Generate `n` synthetic rows with id and name."""
    return [{"id": i, "name": f"Name{i}"} for i in range(1, n + 1)]


def test_characters_pagination_first_page(monkeypatch):
    """Page 1 (10/page): first 10 IDs, has_next=True, has_prev=False."""
    data = _fake_list(50)

    async def fake_list(session, sort, order, page, page_size):
        rows = sorted(data, key=lambda x: x["id"])
        start = (page - 1) * page_size
        end = start + page_size
        return rows[start:end], len(rows)

    monkeypatch.setattr(crud, "list_characters", fake_list)

    client = TestClient(app)
    r = client.get("/characters?page=1&page_size=10&sort=id&order=asc")
    assert r.status_code == 200
    j = r.json()
    assert j["page"] == 1
    assert j["page_size"] == 10
    assert j["total_count"] == 50
    assert j["total_pages"] == 5
    assert j["has_prev"] is False
    assert j["has_next"] is True
    assert [x["id"] for x in j["results"]] == list(range(1, 11))


def test_characters_pagination_middle_page(monkeypatch):
    """Page 2 of 3 (10/page): IDs 11..20, has_prev/has_next both True."""
    data = _fake_list(25)

    async def fake_list(session, sort, order, page, page_size):
        rows = sorted(data, key=lambda x: x["id"])
        start = (page - 1) * page_size
        end = start + page_size
        return rows[start:end], len(rows)

    monkeypatch.setattr(crud, "list_characters", fake_list)

    client = TestClient(app)
    r = client.get("/characters?page=2&page_size=10&sort=id&order=asc")
    j = r.json()
    assert j["page"] == 2
    assert j["total_pages"] == 3
    assert j["has_prev"] is True
    assert j["has_next"] is True
    assert [x["id"] for x in j["results"]] == list(range(11, 21))


def test_characters_pagination_last_page(monkeypatch):
    """Last page: only the final row appears; has_next=False."""
    data = _fake_list(21)

    async def fake_list(session, sort, order, page, page_size):
        rows = sorted(data, key=lambda x: x["id"])
        start = (page - 1) * page_size
        end = start + page_size
        return rows[start:end], len(rows)

    monkeypatch.setattr(crud, "list_characters", fake_list)

    client = TestClient(app)
    r = client.get("/characters?page=3&page_size=10&sort=id&order=asc")
    j = r.json()
    assert j["page"] == 3
    assert j["total_pages"] == 3
    assert j["has_prev"] is True
    assert j["has_next"] is False
    assert [x["id"] for x in j["results"]] == [21]


def test_characters_pagination_beyond_last_returns_empty(monkeypatch):
    """Beyond last page: results are empty but pagination metadata is consistent."""
    data = _fake_list(15)

    async def fake_list(session, sort, order, page, page_size):
        rows = sorted(data, key=lambda x: x["id"])
        start = (page - 1) * page_size
        end = start + page_size
        return rows[start:end], len(rows)

    monkeypatch.setattr(crud, "list_characters", fake_list)

    client = TestClient(app)
    r = client.get("/characters?page=5&page_size=10&sort=id&order=asc")
    j = r.json()
    assert j["page"] == 5
    assert j["total_pages"] == 2
    assert j["results"] == []

"""HTTP route tests for /characters.

Verifies that /characters returns sorted results and basic pagination metadata
when the CRUD layer is mocked.
"""

from fastapi.testclient import TestClient
from app.main import app
from app import crud


def test_characters_route_sorted_by_name(monkeypatch):
    """Return results sorted by name ASC with correct total_count."""

    async def fake_list(session, sort, order, page, page_size):
        rows = [{"id": 2, "name": "Beth"}, {"id": 1, "name": "Alice"}]
        rows = sorted(rows, key=lambda x: x["name"].lower(), reverse=(order == "desc"))
        total = 2
        start = (page - 1) * page_size
        end = start + page_size
        return rows[start:end], total

    monkeypatch.setattr(crud, "list_characters", fake_list)

    client = TestClient(app)
    resp = client.get("/characters?sort=name&order=asc&page=1&page_size=50")
    assert resp.status_code == 200
    j = resp.json()
    assert j["total_count"] == 2
    assert [x["name"] for x in j["results"]] == ["Alice", "Beth"]

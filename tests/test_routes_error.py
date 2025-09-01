"""HTTP error path tests.

Ensures /characters surfaces a 400 when the CRUD layer signals an invalid query.
"""

from fastapi.testclient import TestClient
from app.main import app
from app import crud


def test_characters_route_400_when_name_not_string(monkeypatch):
    """Surface HTTP 400 with a clear error message on bad query/sort."""

    async def bad_list(*a, **k):
        raise ValueError("bad sort")

    monkeypatch.setattr(crud, "list_characters", bad_list)

    client = TestClient(app)
    resp = client.get("/characters?sort=name&order=asc")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid sort parameter or query"

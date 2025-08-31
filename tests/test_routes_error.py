from fastapi.testclient import TestClient
from app.main import app
from app import api

def test_characters_route_400_when_name_not_string(monkeypatch):
    async def fake_get_characters():
        # name is None -> calling .lower() throws AttributeError
        return [{"id": 1, "name": None}]

    monkeypatch.setattr(api, "get_characters", fake_get_characters)
    client = TestClient(app)
    resp = client.get("/characters?sort=name&order=asc")
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"] == "Invalid sort parameter"
from fastapi.testclient import TestClient
from app.main import app
from app import api


def test_characters_route_sorted_by_name(monkeypatch):
    # async fake for get_characters()
    async def fake_get_characters():
        return [{"id": 2, "name": "Beth"}, {"id": 1, "name": "Alice"}]

    monkeypatch.setattr(api, "get_characters", fake_get_characters)

    client = TestClient(app)
    resp = client.get("/characters?sort=name&order=asc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert [x["name"] for x in data["results"]] == ["Alice", "Beth"]


def test_healthcheck_route(monkeypatch):
    # async fake for quick_upstream_probe()
    async def fake_probe():
        return True

    monkeypatch.setattr(api, "quick_upstream_probe", fake_probe)
    monkeypatch.setattr(api, "cache_info", lambda: (True, 1.23))

    client = TestClient(app)
    resp = client.get("/healthcheck")
    assert resp.status_code == 200
    j = resp.json()
    assert j["status"] == "ok"
    assert j["upstream_ok"] is True
    assert j["cache_populated"] is True
    assert j["cache_age_sec"] == 1.23

def test_root_redirects_to_docs():
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/")
    assert resp.status_code in (307, 302)
    assert resp.headers["location"].endswith("/docs")
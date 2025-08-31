import respx
import httpx
from app.clients import BASE_URL

@respx.mock
def test_characters_lazy_bootstrap_and_sort(client):
    page = {
        "info": {"next": None},
        "results": [
            {"id": 2, "name": "Morty", "status": "Alive", "species": "Human",
             "origin": {"name": "Earth (C-137)"}},
            {"id": 1, "name": "Rick", "status": "Alive", "species": "Human",
             "origin": {"name": "Earth (Replacement Dimension)"}},
        ],
    }
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=page))

    # first call bootstraps and sorts by name
    r1 = client.get("/characters?sort_by=name&order=asc&limit=10&offset=0")
    assert r1.status_code == 200
    assert [c["name"] for c in r1.json()] == ["Morty", "Rick"]

    # second call sorted by id desc
    r2 = client.get("/characters?sort_by=id&order=desc&limit=10&offset=0")
    assert r2.status_code == 200
    assert [c["id"] for c in r2.json()] == [2, 1]


def test_characters_invalid_sort(client):
    r = client.get("/characters?sort_by=foobar")
    assert r.status_code == 422

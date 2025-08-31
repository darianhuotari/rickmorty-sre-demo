import respx
import httpx
from app.clients import BASE_URL

@respx.mock
def test_sync_endpoint_idempotent(client):
    page = {
        "info": {"next": None},
        "results": [
            {"id": 10, "name": "Summer", "status": "Alive", "species": "Human",
             "origin": {"name": "Earth (C-137)"}},
        ],
    }
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=page))

    r1 = client.post("/sync")
    assert r1.status_code == 200
    first_total = r1.json()["total"]

    r2 = client.post("/sync")
    assert r2.status_code == 200
    second_total = r2.json()["total"]

    # Assert idempotency — count should not increase
    assert second_total == first_total
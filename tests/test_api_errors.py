import pytest
from app import api

def test_db_error_returns_503(client):
    def broken_get_db():
        yield None
        raise RuntimeError("DB down")

    client.app.dependency_overrides[api.get_db] = broken_get_db

    r = client.get("/characters")
    assert r.status_code == 503
    assert "DB query failed" in r.text

def test_rate_limit_exceeded(client):
    # hit the endpoint >60 times to trigger limit
    last_response = None
    for _ in range(65):
        last_response = client.get("/characters")

    assert last_response.status_code == 429
    assert "rate limit" in last_response.text.lower()
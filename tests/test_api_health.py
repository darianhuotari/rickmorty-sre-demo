def test_health_ok(client):
    r = client.get("/healthcheck")
    assert r.status_code == 200
    body = r.json()
    assert "checks" in body
    assert "database" in body["checks"]

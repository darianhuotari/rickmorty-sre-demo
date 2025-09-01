"""Rate limiting tests using slowapi.

We add a temporary test-only route with a very low limit (2/second) to
deterministically assert a 429 and validate the error body. We avoid asserting
X-RateLimit-* headers because SlowAPI versions differ on when they are added.
"""

from fastapi import Request
from fastapi.testclient import TestClient
import app.main as app_main


def test_temp_route_hits_429_quickly():
    """A test-only route limited to 2/second should return 429 on the 3rd rapid call."""

    # Define a temporary route with a very small limit
    @app_main.limiter.limit("2/second")
    async def _rl_test(request: Request):  # slowapi requires `request`
        return {"ok": True}

    # Mount under a unique path so we don't clash with existing app routes
    path = "/_rltest"
    if not any(getattr(r, "path", None) == path for r in app_main.app.router.routes):
        app_main.app.add_api_route(path, _rl_test, methods=["GET"])

    client = TestClient(app_main.app)

    # First two calls should be 200, third should be 429
    r1 = client.get(path)
    r2 = client.get(path)
    r3 = client.get(path)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert r3.headers.get("content-type", "").startswith("application/problem+json")
    body = r3.json()
    assert body["status"] == 429
    assert body["title"] == "Too Many Requests"
    assert "rate limit" in body["detail"].lower()

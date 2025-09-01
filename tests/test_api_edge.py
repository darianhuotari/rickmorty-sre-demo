"""Edge and exception-path tests for the upstream HTTP client.

Exercises:
* Retry exhaustion -> HTTPException(503)
* Success branch of quick_upstream_probe() when status_code == 200
"""

import pytest
from app import api


@pytest.mark.asyncio
async def test_fetch_all_characters_exhausts_retries_and_raises(monkeypatch):
    """Exhaust retries on continuous 500s and surface HTTPException(503)."""

    class FakeResp:
        status_code = 500
        headers = {}

        def raise_for_status(self):
            # Simulate httpx raising for 500 to match code path after retry
            raise api.httpx.HTTPStatusError("err", request=None, response=None)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):  # always returns a 500 response
            return FakeResp()

    # Speed up backoff; keep retries small
    monkeypatch.setattr(api, "MAX_RETRIES", 2, raising=False)
    monkeypatch.setattr(api, "REQUEST_TIMEOUT", 0.01, raising=False)
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda *a, **k: FakeClient())

    with pytest.raises(api.HTTPException) as excinfo:
        await api.fetch_all_characters()
    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_quick_upstream_probe_true_branch(monkeypatch):
    """Return True when upstream GET returns HTTP 200."""

    class FakeResp:
        status_code = 200

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(api.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    ok = await api.quick_upstream_probe()
    assert ok is True

"""Happy-path HTTP behavior for the upstream API client.

Exercises:
* 429/5xx retry/backoff logic
* Pagination across multiple pages
* Transient transport error -> retry then success
* quick_upstream_probe false path on exception
* cache_info() empty state
"""

import pytest
from app import api


@pytest.mark.asyncio
async def test_fetch_all_characters_retries_then_succeeds(monkeypatch):
    """Retry on 500/429 then fetch page1+page2; return combined results."""

    class FakeResp:
        def __init__(self, status_code, payload=None, headers=None):
            self.status_code = status_code
            self._payload = payload or {}
            self.headers = headers or {}

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise api.httpx.HTTPStatusError("err", request=None, response=None)

    # Two pages of data
    page1_ok = FakeResp(
        200,
        {
            "results": [{"id": 1, "name": "A"}],
            "info": {"next": "yes"},
        },
    )
    page2_ok = FakeResp(
        200,
        {
            "results": [{"id": 2, "name": "B"}],
            "info": {"next": None},
        },
    )

    # Call sequence: 500 -> 429 -> 200 (p1) -> 200 (p2)
    calls = [
        FakeResp(500),
        FakeResp(429, headers={"Retry-After": "0"}),
        page1_ok,
        page2_ok,
    ]
    idx = {"i": 0}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, timeout=None):
            i = idx["i"]
            idx["i"] += 1
            return calls[i]

    monkeypatch.setattr(api.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    monkeypatch.setattr(api, "MAX_RETRIES", 5, raising=False)

    results = await api.fetch_all_characters()
    assert [r["id"] for r in results] == [1, 2]


@pytest.mark.asyncio
async def test_fetch_all_characters_transport_error_then_success(monkeypatch):
    """Retry on transport error; then succeed with empty page."""

    class FakeTransportError(Exception):
        pass

    class FakeResp:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload
            self.headers = {}

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    # First call raises; second returns OK.
    calls = [FakeTransportError(), FakeResp({"results": [], "info": {"next": None}})]
    idx = {"i": 0}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, timeout=None):
            i = idx["i"]
            idx["i"] += 1
            v = calls[i]
            if isinstance(v, Exception):
                # simulate httpx.TransportError
                raise api.httpx.TransportError("boom")
            return v

    monkeypatch.setattr(api.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    results = await api.fetch_all_characters()
    assert results == []  # empty page returned after retry


@pytest.mark.asyncio
async def test_quick_upstream_probe_returns_false_on_exception(monkeypatch):
    """Return False when GET raises a timeout/transport exception."""

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):  # simulate exception during GET
            raise api.httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(api.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    ok = await api.quick_upstream_probe()
    assert ok is False  # covers false branch lines


def test_cache_info_empty():
    """Report populated=False, age=None when cache is empty."""
    api._cache["ts"] = 0
    api._cache["data"] = None
    populated, age = api.cache_info()
    assert populated is False and age is None

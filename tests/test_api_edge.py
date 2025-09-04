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


@pytest.mark.asyncio
async def test_request_with_retry_honors_retry_after_seconds(monkeypatch):
    """
    First call returns 429 with Retry-After: 1, second call returns 200.
    We monkeypatch asyncio.sleep to capture the delay and avoid real sleeping.
    """
    calls = {"n": 0}

    class Resp429:
        status_code = 429
        headers = {"Retry-After": "1"}

        def raise_for_status(self):  # not reached on retry path
            pass

    class Resp200:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

    class FakeClient:
        async def get(self, *a, **k):
            calls["n"] += 1
            return Resp429() if calls["n"] == 1 else Resp200()

    # Capture sleeps (no real delay)
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        return None

    # Make backoff deterministic (no jitter influence if header missing)
    monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(api, "MAX_RETRIES", 3, raising=False)
    monkeypatch.setattr(api, "REQUEST_TIMEOUT", 0.01, raising=False)

    client = FakeClient()
    r = await api._request_with_retry(client, url="http://example.test", params={})
    assert r.status_code == 200
    # We should have slept exactly once, obeying Retry-After header (1s)
    assert slept == [1.0]


def test_parse_retry_after_http_date_future_branch(monkeypatch):
    """_parse_retry_after(): cover try-block success when parsed HTTP-date is in the future.

    We monkeypatch parsedate_to_datetime to return a stub object that:
      * has tzinfo,
      * implements __sub__ to yield a positive timedelta (via .total_seconds()),
      * implements .now(tz) (ignored by our __sub__, but present to mirror datetime API).
    """
    from app import api

    class _Future:
        tzinfo = object()

        def now(self, _tz):
            # Return any object; __sub__ ignores the 'other' operand.
            return object()

        def __sub__(self, _other):
            class _Delta:
                def total_seconds(self):
                    return 12.34  # simulate a future time 12.34s ahead

            return _Delta()

    monkeypatch.setattr(api, "parsedate_to_datetime", lambda _v: _Future())
    secs = api._parse_retry_after("Mon, 01 Jan 2099 00:00:00 GMT")
    assert secs == pytest.approx(12.34, rel=1e-6)


def test_parse_retry_after_exception_branch(monkeypatch):
    """_parse_retry_after(): cover the `except Exception: pass` path."""
    from app import api

    def _raise(_):
        raise RuntimeError("boom")

    # Force parsedate_to_datetime to raise so we hit the except-block and return None
    monkeypatch.setattr(api, "parsedate_to_datetime", _raise)
    assert api._parse_retry_after("Mon, 01 Jan 2099 00:00:00 GMT") is None

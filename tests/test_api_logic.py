import asyncio
from typing import List, Dict, Any

import pytest
from app import api


def _sample_raw() -> List[Dict[str, Any]]:
    # Mix of characters; only 1 & 4 should pass the filter.
    return [
        {  # passes (Human, Alive, Earth*)
            "id": 1,
            "name": "Beth Smith",
            "status": "Alive",
            "species": "Human",
            "origin": {"name": "Earth (Replacement Dimension)"},
            "image": "img1",
            "url": "u1",
        },
        {  # fails (Dead)
            "id": 2,
            "name": "Some Human",
            "status": "Dead",
            "species": "Human",
            "origin": {"name": "Earth (C-137)"},
            "image": "img2",
            "url": "u2",
        },
        {  # fails (Alien)
            "id": 3,
            "name": "Krombopulos Michael",
            "status": "Alive",
            "species": "Alien",
            "origin": {"name": "Earth (C-137)"},
            "image": "img3",
            "url": "u3",
        },
        {  # passes (Human, Alive, Earth*)
            "id": 4,
            "name": "Jerry Smith",
            "status": "Alive",
            "species": "Human",
            "origin": {"name": "Earth (Unknown)"},
            "image": "img4",
            "url": "u4",
        },
        {  # fails (Not Earth)
            "id": 5,
            "name": "Gazorpian",
            "status": "Alive",
            "species": "Human",
            "origin": {"name": "Gazorpazorp"},
            "image": "img5",
            "url": "u5",
        },
    ]


def test_filter_character_results_filters_and_shapes():
    raw = _sample_raw()
    filtered = api.filter_character_results(raw)

    # Only two should pass
    assert len(filtered) == 2
    ids = sorted(x["id"] for x in filtered)
    assert ids == [1, 4]

    # Shape is slimmed to expected keys
    for item in filtered:
        assert set(item.keys()) == {
            "id",
            "name",
            "status",
            "species",
            "origin",
            "image",
            "url",
        }
        assert item["origin"].startswith("Earth")


@pytest.mark.asyncio
async def test_get_characters_uses_cache(monkeypatch):
    # Make cache effectively long for this test
    monkeypatch.setattr(api, "CACHE_TTL", 60, raising=False)

    calls = {"n": 0}

    async def fake_fetch_all():
        calls["n"] += 1
        return _sample_raw()

    # Clear cache before test
    api._cache["ts"] = 0
    api._cache["data"] = None

    monkeypatch.setattr(api, "fetch_all_characters", fake_fetch_all)

    # First call populates cache
    first = await api.get_characters()
    # Second call should use cache (no new fetch)
    second = await api.get_characters()

    assert calls["n"] == 1
    assert first == second
    assert len(first) == 2  # after filtering


@pytest.mark.asyncio
async def test_get_characters_cache_expires(monkeypatch):
    # Very short TTL so it expires
    monkeypatch.setattr(api, "CACHE_TTL", 0.1, raising=False)

    calls = {"n": 0}

    async def fake_fetch_all():
        calls["n"] += 1
        return _sample_raw()

    # Reset cache
    api._cache["ts"] = 0
    api._cache["data"] = None

    monkeypatch.setattr(api, "fetch_all_characters", fake_fetch_all)

    _ = await api.get_characters()
    assert calls["n"] == 1

    # Wait for TTL to expire
    await asyncio.sleep(0.12)

    _ = await api.get_characters()
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_quick_upstream_probe_mocked(monkeypatch):
    # Mock httpx.AsyncClient so we don't do real I/O
    class FakeResp:
        status_code = 200

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(api.httpx, "AsyncClient", FakeClient)
    ok = await api.quick_upstream_probe()
    assert ok is True

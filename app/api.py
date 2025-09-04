"""External Rick & Morty API client and lightweight data cache.

This module encapsulates all interactions with the public Rick & Morty REST API,
including robust retry/backoff behavior, pagination handling, and a very small
in-process cache for upstream call coalescing. It also exposes a quick upstream probe
used by the application's health check.
"""

import logging
import os
import time
import random
import asyncio
from typing import List, Dict, Any, Tuple

import httpx
from fastapi import HTTPException
from email.utils import parsedate_to_datetime

BASE_URL = "https://rickandmortyapi.com/api/character"
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))  # seconds
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "10"))

# very simple in-memory cache to avoid hammering upstream on every request
_cache: Dict[str, Any] = {"ts": 0.0, "data": None}

log = logging.getLogger(__name__)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header value.

    Supports both integer seconds and HTTP-date formats.

    Args:
        value: Header value or ``None``.

    Returns:
        Seconds to wait as a float, or ``None`` if the value is missing or invalid.
    """

    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        # HTTP-date -> seconds from now
        dt = parsedate_to_datetime(value)
        if dt is not None:
            return max(0.0, (dt - dt.now(dt.tzinfo)).total_seconds())
    except Exception:
        pass
    return None


async def _request_with_retry(
    client: httpx.AsyncClient, url: str, params: Dict[str, Any]
):
    """Issue a resilient GET request with exponential backoff and retry.

    Retries on HTTP 429 and 5xx responses, honoring the `Retry-After` header when present,
    and on common transient network errors (timeouts, transport issues).

    Args:
        client: An existing `httpx.AsyncClient` to use for the request.
        url: Target URL to fetch.
        params: Query parameters to include in the GET.

    Returns:
        The successful `httpx.Response`.

    Raises:
        HTTPException: If all retries are exhausted (503).
    """
    backoff = 0.5
    attempt = 0

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = await client.get(url, params=params, timeout=REQUEST_TIMEOUT)

            if r.status_code == 429 or 500 <= r.status_code < 600:
                ra_hdr = r.headers.get("Retry-After")
                ra = _parse_retry_after(ra_hdr)
                delay = (
                    ra if ra is not None else backoff + random.random() * 0.25
                )  # nosec B311

                log.warning(
                    "upstream.retry status=%d attempt=%d/%d url=%s retry_after=%s delay=%.3fs",
                    r.status_code,
                    attempt,
                    MAX_RETRIES,
                    url,
                    ra_hdr,
                    delay,
                )

                await asyncio.sleep(delay)
                backoff = min(backoff * 2, 8.0)
                continue

            r.raise_for_status()
            if attempt > 1:
                log.info(
                    "upstream.recovered attempt=%d url=%s status=%d",
                    attempt,
                    url,
                    r.status_code,
                )
            return r

        except (
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.RemoteProtocolError,
            httpx.TransportError,
        ) as exc:
            log.warning(
                "upstream.error attempt=%d/%d url=%s err=%r backoff=%.3fs",
                attempt,
                MAX_RETRIES,
                url,
                exc,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 8.0)

    # If we got here, all attempts failed
    log.error(
        "upstream.failed url=%s attempts=%d detail=%s",
        url,
        MAX_RETRIES,
        "exhausted retries",
    )
    raise HTTPException(
        status_code=503, detail="Upstream API unavailable after retries"
    )


# ---------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------


async def fetch_all_characters() -> List[Dict[str, Any]]:
    """Fetch all characters from the upstream API with pagination and retries.

    Walks through pages until `info.next` is absent. Uses `_request_with_retry`
    to be resilient to throttling and transient failures.

    Returns:
        A list of raw character dicts as provided by the Rick & Morty API.
    """
    results: List[Dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            params = {"page": page}
            resp = await _request_with_retry(client, BASE_URL, params)
            data = resp.json()
            results.extend(data.get("results", []))
            if not (data.get("info") or {}).get("next"):
                break
            page += 1
    return results


def filter_character_results(characters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply assignment filters and return a slimmed payload.

    Filters:
      * species == "Human"
      * status == "Alive"
      * origin starts with "Earth"

    Args:
        characters: Raw character dicts from the upstream API.

    Returns:
        A list of filtered character dicts with just relevant fields.
    """
    out: List[Dict[str, Any]] = []
    for ch in characters:
        if ch.get("species") != "Human" or ch.get("status") != "Alive":
            continue
        origin = (ch.get("origin") or {}).get("name") or ""
        if origin.startswith("Earth"):
            out.append(
                {
                    "id": ch.get("id"),
                    "name": ch.get("name"),
                    "status": ch.get("status"),
                    "species": ch.get("species"),
                    "origin": origin,
                    "image": ch.get("image"),
                    "url": ch.get("url"),
                }
            )
    return out


async def get_characters() -> List[Dict[str, Any]]:
    """Return filtered characters using a simple in-process cache.

    The first call fetches from upstream and caches the filtered results for `CACHE_TTL`
    seconds. Subsequent calls within the TTL return the cached value.

    Returns:
        Filtered character dicts (list).
    """
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < CACHE_TTL:
        return _cache["data"]

    raw = await fetch_all_characters()
    filtered = filter_character_results(raw)

    _cache["ts"] = time.time()
    _cache["data"] = filtered
    return filtered


def cache_info() -> Tuple[bool, float | None]:
    """Report cache state and age.

    Returns:
        Tuple of:
          * populated (bool): Whether the cache currently has data.
          * age_sec (float | None): Age of the cache in seconds, or None if empty.
    """
    if _cache["ts"] == 0:
        return False, None
    return (_cache["data"] is not None, round(time.time() - _cache["ts"], 2))


async def quick_upstream_probe() -> bool:
    """Perform a lightweight upstream health probe.

    Returns:
        True if the upstream root API endpoint returns HTTP 200,
        otherwise False (including exceptions).
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("https://rickandmortyapi.com/api")
            return r.status_code == 200
    except Exception:
        return False

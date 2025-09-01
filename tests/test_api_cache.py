"""Cache inspection tests for the upstream API module.

Covers positive-path behavior of `cache_info()` when the in-process cache
is populated and recently refreshed.
"""

import time
from app import api


def test_cache_info_populated():
    """Report populated=True and a reasonable age when cache has data."""
    # Save & restore to avoid test leakage
    old_ts, old_data = api._cache["ts"], api._cache["data"]
    try:
        # Pretend we populated cache ~1.0s ago
        api._cache["ts"] = time.time() - 1.0
        api._cache["data"] = []  # anything non-None counts as "populated"

        populated, age = api.cache_info()
        assert populated is True
        assert 0.9 <= age <= 2.0  # allow a little timing wiggle
    finally:
        api._cache["ts"], api._cache["data"] = old_ts, old_data

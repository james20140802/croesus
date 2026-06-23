import time
from croesus.web.cache import TTLCache


def test_ttl_cache_caches_then_expires():
    cache = TTLCache(ttl_seconds=0.05)
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return calls["n"]

    assert cache.get_or_set("k", factory) == 1
    assert cache.get_or_set("k", factory) == 1  # 캐시 hit
    time.sleep(0.06)
    assert cache.get_or_set("k", factory) == 2  # 만료 후 재계산


def test_ttl_cache_invalidate():
    cache = TTLCache(ttl_seconds=100)
    cache.get_or_set("k", lambda: 1)
    cache.invalidate()
    assert cache.get_or_set("k", lambda: 2) == 2

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


from datetime import date
from fastapi.testclient import TestClient
from croesus.web import create_app
from croesus.web import services
from croesus.web.viewmodels import MacroView


def _client_with(monkeypatch, **patches):
    for name, value in patches.items():
        monkeypatch.setattr(services, name, lambda *a, _v=value, **k: _v)
    return TestClient(create_app("storage/croesus.duckdb"), raise_server_exceptions=False)


def test_macro_page_renders(monkeypatch):
    view = MacroView(
        date=date(2026, 6, 22), regime="Goldilocks", positioning="Aggressive",
        regime_confidence=0.8, amplifier_score=30.0, confirmation_score=0.4,
        warnings=[], opportunities=[], regime_methods={}, history=[],
    )
    # read 연결을 막기 위해 라우트가 호출하는 build_macro_view를 패치
    monkeypatch.setattr("croesus.web.routes.macro.build_macro_view", lambda conn: view)
    monkeypatch.setattr("croesus.web.routes.macro.get_read_connection",
                        __import__("contextlib").contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("storage/croesus.duckdb"), raise_server_exceptions=False)
    resp = client.get("/macro")
    assert resp.status_code == 200
    assert "Goldilocks" in resp.text
    assert "Aggressive" in resp.text

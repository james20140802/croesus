from fastapi.testclient import TestClient
from tests._web_helpers import make_app


def test_healthz_ok():
    client = TestClient(make_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_home_renders(monkeypatch):
    from croesus.web.viewmodels import HomeView
    hv = HomeView(macro=None, actions=[], action_count=0, opportunity_count=0,
                  drift_alerts=[], screening_count=0, freshness=[])
    monkeypatch.setattr("croesus.web.routes.home.build_home_view", lambda conn: hv)
    monkeypatch.setattr("croesus.web.routes.home.get_read_connection",
                        __import__("contextlib").contextmanager(lambda p: iter([None])))
    client = TestClient(make_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Croesus" in resp.text

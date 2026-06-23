from fastapi.testclient import TestClient
from tests._web_helpers import make_app


def test_healthz_ok():
    client = TestClient(make_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_home_renders():
    client = TestClient(make_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Croesus" in resp.text

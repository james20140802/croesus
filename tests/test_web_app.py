from fastapi.testclient import TestClient
from tests._web_helpers import make_app


def _table_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [name],
        ).fetchone()
    )


def test_startup_migrates_schema_so_new_tables_dont_500(tmp_path):
    # Reproduces the normalized-DCF 500: an existing DB created before a schema
    # addition is missing the new table. Starting the app must migrate it up so
    # the page renders instead of raising a CatalogException.
    from croesus.db.connection import get_connection
    from croesus.db.migrate import migrate

    db = tmp_path / "web.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        conn.execute("DROP TABLE normalized_dcf_snapshots")  # simulate a pre-#59 DB
        assert not _table_exists(conn, "normalized_dcf_snapshots")

    # Entering the TestClient context runs the lifespan startup (our migrate).
    with TestClient(make_app(db), raise_server_exceptions=False) as client:
        resp = client.get("/opportunities?methodology=normalized_dcf")
        assert resp.status_code == 200

    with get_connection(db) as conn:
        assert _table_exists(conn, "normalized_dcf_snapshots")


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

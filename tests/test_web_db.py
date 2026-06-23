import duckdb
import pytest
from croesus.web.db import get_read_connection, get_write_connection, DataUpdatingError


def _make_db(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (42)")
    con.close()


def test_read_connection_reads(tmp_path):
    db = tmp_path / "x.duckdb"
    _make_db(db)
    with get_read_connection(db) as conn:
        assert conn.execute("SELECT x FROM t").fetchone()[0] == 42


def test_read_connection_raises_when_locked(tmp_path):
    db = tmp_path / "x.duckdb"
    _make_db(db)
    holder = duckdb.connect(str(db))  # 외부 read-write 점유
    try:
        with pytest.raises(DataUpdatingError):
            with get_read_connection(db):
                pass
    finally:
        holder.close()


def test_write_connection_writes(tmp_path):
    db = tmp_path / "x.duckdb"
    _make_db(db)
    with get_write_connection(db) as conn:
        conn.execute("INSERT INTO t VALUES (7)")
    with get_read_connection(db) as conn:
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 2


from fastapi import APIRouter
from fastapi.testclient import TestClient
from croesus.web import create_app


def test_app_returns_503_on_data_updating(tmp_path):
    app = create_app(tmp_path / "missing.duckdb")
    boom = APIRouter()

    @boom.get("/boom")
    def _boom():
        raise DataUpdatingError("locked")

    app.include_router(boom)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 503
    assert "동기화" in resp.text

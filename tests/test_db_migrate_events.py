"""migrate()가 레거시 events 테이블(자산 단위 스키마와 충돌)을 고치는지 검증."""
from __future__ import annotations

import duckdb

from croesus.db.migrate import migrate


def _events_columns(db_path) -> list[str]:
    conn = duckdb.connect(str(db_path))
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info('events')").fetchall()]
    finally:
        conn.close()


def test_migrate_replaces_legacy_events_schema(tmp_path):
    db = tmp_path / "legacy.duckdb"
    # 구(舊) FOMC 매크로 이벤트 스키마를 흉내내 미리 만들어 둔다 (asset_id 없음)
    conn = duckdb.connect(str(db))
    conn.execute(
        "CREATE TABLE events (date DATE, category TEXT, magnitude DOUBLE, scope TEXT, metadata JSON)"
    )
    conn.execute("INSERT INTO events VALUES (DATE '2025-06-18', 'fomc', 0.0, 'US', '{}')")
    conn.close()

    migrate(db)

    cols = _events_columns(db)
    # 충돌하던 구 스키마가 자산 단위 스키마로 교체되어야 한다
    assert "asset_id" in cols
    assert "as_of_date" in cols
    assert "event_type" in cols
    # 구 스키마 전용 컬럼은 사라진다
    assert "category" not in cols
    assert "scope" not in cols


def test_migrate_keeps_correct_events_schema(tmp_path):
    # 이미 올바른 스키마면 그대로 두고(드롭하지 않고) 멱등하게 동작
    db = tmp_path / "ok.duckdb"
    migrate(db)  # 최초 생성
    conn = duckdb.connect(str(db))
    conn.execute(
        "INSERT INTO events (asset_id, as_of_date, event_type, source) "
        "VALUES ('US_EQ_AAPL', DATE '2026-06-26', 'abnormal_return', 'prices_daily')"
    )
    conn.close()

    migrate(db)  # 재실행 — 데이터가 보존되어야 한다(드롭 안 함)

    conn = duckdb.connect(str(db))
    try:
        n = conn.execute("SELECT count(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    assert n == 1

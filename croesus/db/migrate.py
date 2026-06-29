from __future__ import annotations

from pathlib import Path

from croesus.db.connection import get_connection

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _drop_legacy_events_table(conn) -> None:
    """레거시 'events' 테이블(FOMC 매크로 이벤트, 컬럼: date/category/scope…)을 감지해 제거.

    오퍼튜니티 엔진의 자산 단위 events 스키마(asset_id, as_of_date, event_type…)와
    이름이 충돌한다. ``CREATE TABLE IF NOT EXISTS``는 기존 테이블을 갱신하지 못하므로,
    ``asset_id`` 컬럼이 없는 구(舊) 스키마가 남아 있으면 schema.sql 적용 전에 드롭한다.
    구 테이블을 읽는 현행 코드는 없어 안전하다.
    """
    exists = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'events' LIMIT 1"
    ).fetchone()
    if not exists:
        return  # 테이블이 없으면 schema.sql이 올바른 스키마로 새로 만든다
    cols = [r[1] for r in conn.execute("PRAGMA table_info('events')").fetchall()]
    if "asset_id" not in cols:
        conn.execute("DROP TABLE events")


def migrate(db_path: str | Path | None = None) -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection(db_path) as conn:
        _drop_legacy_events_table(conn)
        conn.execute(schema)


if __name__ == "__main__":
    migrate()

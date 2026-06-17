from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_migrate_creates_events_table(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        cols = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'events'"
            ).fetchall()
        }
    assert cols == {
        "asset_id",
        "as_of_date",
        "event_type",
        "direction",
        "magnitude",
        "detail",
        "source",
        "created_at",
    }


def test_event_model_and_constants() -> None:
    from croesus.events.models import (
        DIRECTION_UP,
        EVENT_ABNORMAL_VOLUME,
        Event,
        EventScanResult,
    )

    event = Event(
        asset_id="US_EQ_AAPL",
        as_of_date=date(2026, 6, 1),
        event_type=EVENT_ABNORMAL_VOLUME,
        direction=DIRECTION_UP,
        magnitude=3.2,
        detail="volume 3.2σ above 21d mean",
        source="prices_daily",
    )
    assert event.event_type == "abnormal_volume"
    assert event.direction == "up"

    result = EventScanResult()
    assert result.scanned == []
    assert result.events == []
    assert result.failed == {}

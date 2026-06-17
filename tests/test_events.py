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


import pandas as pd


def _price_frame(closes: list[float], volumes: list[float]) -> pd.DataFrame:
    n = len(closes)
    start = date(2026, 1, 1)
    return pd.DataFrame(
        {
            "date": [start + pd.Timedelta(days=i) for i in range(n)],
            "close": closes,
            "volume": volumes,
        }
    )


def test_detect_abnormal_volume_flags_spike_only() -> None:
    from croesus.events.detectors import detect_abnormal_volume

    # 30 mildly-varying-volume days (mean ~1000, non-zero std), then a big spike.
    closes = [100.0] * 31
    base_vol = [900.0, 1000.0, 1100.0] * 10  # 30 values, mean 1000, std > 0
    volumes = base_vol + [5000.0]
    event = detect_abnormal_volume("US_EQ_AAPL", date(2026, 2, 1), _price_frame(closes, volumes))
    assert event is not None
    assert event.event_type == "abnormal_volume"
    assert event.direction == "up"
    assert event.magnitude > 2.0
    assert event.source == "prices_daily"

    # A volume DROP is not an event.
    drop = detect_abnormal_volume(
        "US_EQ_AAPL", date(2026, 2, 1), _price_frame(closes, base_vol + [10.0])
    )
    assert drop is None

    # Perfectly flat volume -> zero std -> no event (no divide-by-zero).
    flat = detect_abnormal_volume(
        "US_EQ_AAPL", date(2026, 2, 1), _price_frame(closes, [1000.0] * 31)
    )
    assert flat is None

    # Too little history -> None.
    short = detect_abnormal_volume(
        "US_EQ_AAPL", date(2026, 2, 1), _price_frame([100.0] * 5, [1000.0] * 5)
    )
    assert short is None


def test_detect_abnormal_return_flags_direction() -> None:
    from croesus.events.detectors import detect_abnormal_return

    # 64 days of tiny ±0.1% wiggles, then a +20% jump on the last day.
    closes = [100.0 * (1.0 + 0.001 * ((-1) ** i)) for i in range(64)]
    closes.append(closes[-1] * 1.20)
    volumes = [1000.0] * len(closes)
    up = detect_abnormal_return("US_EQ_AAPL", date(2026, 3, 1), _price_frame(closes, volumes))
    assert up is not None
    assert up.event_type == "abnormal_return"
    assert up.direction == "up"
    assert up.magnitude > 3.0

    # A -20% crash flags 'down' with negative magnitude.
    closes_down = closes[:-1] + [closes[-2] * 0.80]
    down = detect_abnormal_return(
        "US_EQ_AAPL", date(2026, 3, 1), _price_frame(closes_down, volumes)
    )
    assert down is not None
    assert down.direction == "down"
    assert down.magnitude < -3.0

    # Calm series -> no event.
    calm = [100.0 * (1.0 + 0.001 * ((-1) ** i)) for i in range(65)]
    assert detect_abnormal_return(
        "US_EQ_AAPL", date(2026, 3, 1), _price_frame(calm, volumes)
    ) is None

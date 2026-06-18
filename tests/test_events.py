from datetime import date
from pathlib import Path

import pandas as pd

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

    # Perfectly flat price -> all returns 0 -> zero std -> None (no divide-by-zero).
    flat = [100.0] * 65
    assert detect_abnormal_return(
        "US_EQ_AAPL", date(2026, 3, 1), _price_frame(flat, volumes)
    ) is None

    # A 0.0 close yields an inf return (survives dropna) -> NaN std; the detector
    # must reject it, never emit an Event with a NaN/inf magnitude.
    poisoned = [100.0, 0.0] + [100.0] * 63
    result = detect_abnormal_return(
        "US_EQ_AAPL", date(2026, 3, 1), _price_frame(poisoned, [1000.0] * 65)
    )
    assert result is None


def test_detect_recent_disclosure_within_window() -> None:
    from croesus.disclosures.models import Disclosure
    from croesus.events.detectors import detect_recent_disclosure

    def _d(form: str, filed: date) -> Disclosure:
        return Disclosure(
            asset_id="US_EQ_AAPL",
            accession_number=f"{form}-{filed}",
            form_type=form,
            filed_date=filed,
            report_date=None,
            primary_doc_url=None,
            title=None,
        )

    as_of = date(2026, 6, 10)
    # An 8-K filed 3 days ago is within the 7-day window -> event.
    recent = detect_recent_disclosure("US_EQ_AAPL", as_of, [_d("8-K", date(2026, 6, 7))])
    assert recent is not None
    assert recent.event_type == "recent_disclosure"
    assert recent.direction == "neutral"
    assert recent.magnitude == 3.0  # days ago
    assert "8-K" in recent.detail
    assert recent.source == "disclosures"

    # Boundary: filed exactly 7 days ago is inclusive (<= window) -> event.
    edge = detect_recent_disclosure("US_EQ_AAPL", as_of, [_d("8-K", date(2026, 6, 3))])
    assert edge is not None and edge.magnitude == 7.0
    # One day past the window (8 days ago) -> None.
    assert detect_recent_disclosure("US_EQ_AAPL", as_of, [_d("8-K", date(2026, 6, 2))]) is None

    # A filing 30 days ago is outside the window -> None.
    assert detect_recent_disclosure("US_EQ_AAPL", as_of, [_d("10-K", date(2026, 5, 1))]) is None

    # Future-dated filing (> as_of) is ignored -> None.
    assert detect_recent_disclosure("US_EQ_AAPL", as_of, [_d("8-K", date(2026, 6, 20))]) is None

    # No filings -> None.
    assert detect_recent_disclosure("US_EQ_AAPL", as_of, []) is None


def test_detect_valuation_dislocation_direction_and_threshold() -> None:
    from croesus.factors.equity.repository import ValuationSnapshot
    from croesus.events.detectors import detect_valuation_dislocation

    def _snap(upside: float | None) -> ValuationSnapshot:
        return ValuationSnapshot(
            asset_id="US_EQ_AAPL",
            date=date(2026, 6, 1),
            intrinsic_value_per_share=120.0,
            current_price=100.0,
            upside_pct=upside,
            wacc=0.09,
            fcf_growth_rate=0.1,
            terminal_growth_rate=0.025,
            assumptions={},
        )

    as_of = date(2026, 6, 1)
    # +40% upside (price well below intrinsic) -> 'up' dislocation.
    under = detect_valuation_dislocation("US_EQ_AAPL", as_of, _snap(0.40))
    assert under is not None
    assert under.direction == "up"
    assert under.magnitude == 0.40
    assert under.source == "valuation_snapshots"

    # -40% (price above intrinsic) -> 'down'.
    over = detect_valuation_dislocation("US_EQ_AAPL", as_of, _snap(-0.40))
    assert over is not None and over.direction == "down"

    # Within ±25% band -> None.
    assert detect_valuation_dislocation("US_EQ_AAPL", as_of, _snap(0.10)) is None
    # Missing snapshot or upside -> None.
    assert detect_valuation_dislocation("US_EQ_AAPL", as_of, None) is None
    assert detect_valuation_dislocation("US_EQ_AAPL", as_of, _snap(None)) is None


def test_detect_events_aggregates_all_detectors() -> None:
    from croesus.disclosures.models import Disclosure
    from croesus.factors.equity.repository import ValuationSnapshot
    from croesus.events.detectors import detect_events

    # Mild ±0.1% wiggle (non-zero baseline std) then a +30% jump; varied volume
    # then a spike — so both price detectors have a real baseline to fire against.
    wiggle = [100.0 * (1.0 + 0.001 * ((-1) ** i)) for i in range(64)]
    closes = wiggle + [wiggle[-1] * 1.30]                 # +30% jump
    volumes = ([900.0, 1000.0, 1100.0] * 22)[:64] + [9000.0]   # 64 varied + spike
    prices = _price_frame(closes, volumes)
    snapshot = ValuationSnapshot(
        asset_id="US_EQ_AAPL", date=date(2026, 3, 5), intrinsic_value_per_share=150.0,
        current_price=100.0, upside_pct=0.50, wacc=0.09, fcf_growth_rate=0.1,
        terminal_growth_rate=0.025, assumptions={},
    )
    disclosures = [
        Disclosure(
            asset_id="US_EQ_AAPL", accession_number="8-K-1", form_type="8-K",
            filed_date=date(2026, 3, 4), report_date=None, primary_doc_url=None, title=None,
        )
    ]
    as_of = date(2026, 3, 5)

    events = detect_events("US_EQ_AAPL", as_of, prices, snapshot, disclosures)
    types = {e.event_type for e in events}
    assert types == {
        "abnormal_volume",
        "abnormal_return",
        "recent_disclosure",
        "valuation_dislocation",
    }
    # A calm asset with no snapshot and no disclosures yields nothing.
    calm = _price_frame([100.0] * 65, [1000.0] * 65)
    assert detect_events("US_EQ_AAPL", as_of, calm, None, []) == []


def test_event_repository_upserts_idempotently(tmp_path: Path) -> None:
    from croesus.events.models import Event
    from croesus.events.repository import EventRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    as_of = date(2026, 6, 1)
    first = Event(
        asset_id="US_EQ_AAPL", as_of_date=as_of, event_type="abnormal_volume",
        direction="up", magnitude=2.5, detail="v2.5", source="prices_daily",
    )
    with get_connection(db_path) as conn:
        repo = EventRepository(conn)
        assert repo.upsert([first]) == 1
        # Re-scan same (asset, date, type) with a refined magnitude -> still one row.
        updated = Event(
            asset_id="US_EQ_AAPL", as_of_date=as_of, event_type="abnormal_volume",
            direction="up", magnitude=3.1, detail="v3.1", source="prices_daily",
        )
        assert repo.upsert([updated]) == 1
        rows = conn.execute(
            "SELECT asset_id, event_type, magnitude FROM events"
        ).fetchall()
        assert rows == [("US_EQ_AAPL", "abnormal_volume", 3.1)]

        loaded = repo.load_for_date("US_EQ_AAPL", as_of)
        assert len(loaded) == 1
        assert loaded[0].magnitude == 3.1
        assert loaded[0].source == "prices_daily"


def test_run_event_scan_emits_and_persists_events(tmp_path: Path) -> None:
    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.events.scan import run_event_scan
    from croesus.prices.repository import PriceRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    # AAPL gets a volume spike + return jump on the last day; MSFT/NVDA stay calm.
    # Baselines wiggle (non-zero std) so the price detectors have something to
    # fire against — a constant baseline yields std 0 and (correctly) no event.
    n = 65
    base_dates = [date(2026, 1, 1) + pd.Timedelta(days=i) for i in range(n)]
    wiggle = [100.0 * (1.0 + 0.001 * ((-1) ** i)) for i in range(n - 1)]
    spike_close = wiggle + [wiggle[-1] * 1.30]                 # +30% jump
    varied_vol = ([900.0, 1000.0, 1100.0] * 22)[:n]           # 65 mildly-varied
    spike_vol = varied_vol[: n - 1] + [9000.0]                # spike on last day
    calm_close = [100.0 * (1.0 + 0.001 * ((-1) ** i)) for i in range(n)]
    calm_vol = varied_vol

    def _frame(closes, vols):
        return pd.DataFrame(
            {
                "date": base_dates,
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "adjusted_close": closes,
                "volume": vols,
            }
        )

    with get_connection(db_path) as conn:
        seed_us_equities(conn)  # AAPL, MSFT, NVDA
        prices = PriceRepository(conn)
        prices.upsert_daily_prices("US_EQ_AAPL", _frame(spike_close, spike_vol), source="test")
        prices.upsert_daily_prices("US_EQ_MSFT", _frame(calm_close, calm_vol), source="test")
        prices.upsert_daily_prices("US_EQ_NVDA", _frame(calm_close, calm_vol), source="test")

        result = run_event_scan(conn, as_of_date=date(2026, 3, 6))
        stored = conn.execute(
            "SELECT asset_id, event_type FROM events ORDER BY asset_id, event_type"
        ).fetchall()

    assert set(result.scanned) == {"AAPL", "MSFT", "NVDA"}
    assert result.failed == {}
    # Only AAPL fired (volume + return); calm names produced nothing.
    assert stored == [
        ("US_EQ_AAPL", "abnormal_return"),
        ("US_EQ_AAPL", "abnormal_volume"),
    ]


def test_run_event_scan_isolates_per_asset_failure(tmp_path: Path, monkeypatch) -> None:
    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.events import scan as scan_mod
    from croesus.events.scan import run_event_scan
    from croesus.prices.repository import PriceRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    real_detect = scan_mod.detect_events

    def boom(asset_id, as_of_date, prices, snapshot, disclosures):
        if asset_id == "US_EQ_MSFT":
            raise RuntimeError("detector exploded")
        return real_detect(asset_id, as_of_date, prices, snapshot, disclosures)

    monkeypatch.setattr(scan_mod, "detect_events", boom)

    n = 65
    base_dates = [date(2026, 1, 1) + pd.Timedelta(days=i) for i in range(n)]

    def _frame(close, vol):
        return pd.DataFrame(
            {
                "date": base_dates,
                "open": [close] * n,
                "high": [close] * n,
                "low": [close] * n,
                "close": [close] * n,
                "adjusted_close": [close] * n,
                "volume": [vol] * n,
            }
        )

    with get_connection(db_path) as conn:
        seed_us_equities(conn)  # AAPL, MSFT, NVDA
        prices = PriceRepository(conn)
        for aid in ("US_EQ_AAPL", "US_EQ_MSFT", "US_EQ_NVDA"):
            prices.upsert_daily_prices(aid, _frame(100.0, 1000), source="test")
        result = run_event_scan(conn, as_of_date=date(2026, 3, 6))

    # The failing asset is isolated; the others still scan.
    assert result.failed == {"MSFT": "detector exploded"}
    assert "AAPL" in result.scanned and "NVDA" in result.scanned
    assert "MSFT" not in result.scanned


def test_event_scan_registered_in_sync_pipeline() -> None:
    from croesus.jobs.local_sync import default_sync_jobs
    from croesus.jobs.run_status import DOMAINS_BY_NAME

    assert "events" in DOMAINS_BY_NAME
    assert DOMAINS_BY_NAME["events"].job_name == "event_scan"

    jobs = {job.name: job for job in default_sync_jobs()}
    assert "event_scan" in jobs
    scan_job = jobs["event_scan"]
    assert scan_job.domains == ("events",)
    # Needs fresh prices + valuation (daily_run); reacts to new disclosures softly.
    assert scan_job.depends_on == ("daily_run",)
    assert scan_job.soft_depends_on == ("disclosures_run",)

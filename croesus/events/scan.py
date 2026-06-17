from __future__ import annotations

from datetime import date
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.disclosures.repository import DisclosureRepository
from croesus.events.detectors import detect_events
from croesus.events.models import EventScanResult
from croesus.events.repository import EventRepository
from croesus.factors.equity.repository import ValuationSnapshotRepository
from croesus.prices.repository import PriceRepository

# Operating companies are the event subjects (matches the disclosure funnel).
SCAN_ASSET_TYPES = ("equity",)


def run_event_scan(
    conn: duckdb.DuckDBPyConnection,
    *,
    as_of_date: date | None = None,
    log: Callable[[str], None] = print,
) -> EventScanResult:
    """Run the deterministic detectors over every active equity and persist events.

    ``as_of_date`` defaults to the latest price date in the DB. Per-asset failures
    are isolated so one bad series never stops the scan.
    """
    if as_of_date is None:
        as_of_date = _latest_price_date(conn) or date.today()

    assets = [
        a
        for a in AssetRepository(conn).list_active()
        if a.asset_type in SCAN_ASSET_TYPES
    ]
    prices_repo = PriceRepository(conn)
    valuation_repo = ValuationSnapshotRepository(conn)
    disclosure_repo = DisclosureRepository(conn)
    event_repo = EventRepository(conn)
    result = EventScanResult()

    for asset in assets:
        try:
            # Forward-only scan: detectors evaluate the latest available row.
            # ``as_of_date`` defaults to that row's date, so no point-in-time
            # slice is needed here (B2 is not a backtest — spec §정직한 검증 한계).
            # The disclosure/valuation detectors still apply their own ``<= as_of``
            # filtering (filed_date window / SQL date<=as_of) for correctness.
            prices = prices_repo.load_daily_prices(asset.asset_id)
            snapshot = valuation_repo.get(asset.asset_id, as_of_date)
            disclosures = disclosure_repo.load_for_asset(asset.asset_id)
            events = detect_events(
                asset.asset_id, as_of_date, prices, snapshot, disclosures
            )
            event_repo.upsert(events)
            result.scanned.append(asset.symbol)
            result.events.extend(events)
            if events:
                log(f"{asset.symbol}: {len(events)} event(s)")
        except Exception as exc:  # noqa: BLE001 - per-asset failures must not stop the scan.
            result.failed[asset.symbol] = str(exc)
            log(f"failed {asset.symbol}: {exc}")

    return result


def _latest_price_date(conn: duckdb.DuckDBPyConnection) -> date | None:
    row = conn.execute("SELECT MAX(date) FROM prices_daily").fetchone()
    return row[0] if row and row[0] is not None else None

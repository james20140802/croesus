from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.disclosures.models import Disclosure
from croesus.disclosures.repository import DisclosureRepository
from croesus.disclosures.source import DisclosureSource, EdgarDisclosureSource

# US operating companies are the EDGAR filers we care about. ETFs/funds rarely
# file the narrative 8-Ks the event funnel keys on, and non-US filers won't be
# in EDGAR's ticker->CIK map (so they skip naturally), but excluding non-equity
# types up front avoids pointless network calls.
FILER_ASSET_TYPES = ("equity",)


@dataclass(frozen=True)
class DisclosureIngestionResult:
    succeeded: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def ingest_disclosures(
    conn: duckdb.DuckDBPyConnection,
    source: DisclosureSource | None = None,
    *,
    log: Callable[[str], None] = print,
) -> DisclosureIngestionResult:
    """Fetch and upsert recent SEC filings for every active US-equity asset.

    Per-asset failures are recorded and skipped so one unreachable filer never
    stops the run (mirrors ``ingest_daily_prices``).
    """
    source = source or EdgarDisclosureSource()
    assets = [
        a
        for a in AssetRepository(conn).list_active()
        if a.asset_type in FILER_ASSET_TYPES
    ]
    repo = DisclosureRepository(conn)
    result = DisclosureIngestionResult()

    for asset in assets:
        try:
            raw_filings = source.fetch_recent_filings(asset.symbol)
            if not raw_filings:
                result.skipped[asset.symbol] = "no filings returned"
                log(f"skip {asset.symbol}: no filings returned")
                continue
            disclosures = [
                Disclosure.from_raw(asset.asset_id, raw) for raw in raw_filings
            ]
            rows = repo.upsert(disclosures)
            result.succeeded.append(asset.symbol)
            log(f"stored {rows} disclosures for {asset.symbol}")
        except Exception as exc:  # noqa: BLE001 - per-asset failures must not stop the run.
            result.failed[asset.symbol] = str(exc)
            log(f"failed {asset.symbol}: {exc}")

    return result

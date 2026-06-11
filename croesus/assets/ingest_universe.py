"""
Index-universe ingestion into the asset registry (Sprint 008c).

Takes constituents from one or more ``UniverseSource``s, dedups symbols that
appear in several indices (AAPL is in both the S&P 500 and the NASDAQ-100),
and upserts them into ``assets`` idempotently:

  - new symbols are registered as US equities with the standard asset id
    (``make_asset_id`` — the same convention the resolver uses), so a later
    holdings import of the same ticker resolves to this row;
  - existing rows are only *filled in* (name/sector/industry when missing) and
    get their index-membership metadata refreshed — type, source, and every
    manually curated field stay untouched;
  - re-running with the same constituents changes nothing.

Per-ticker metadata enrichment (exchange, fine-grained type) stays lazy via
the resolver — registering ~600 names must not mean ~600 yfinance calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import duckdb

from croesus.assets.identity import make_asset_id
from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.assets.universe_sources.base import UniverseSource

# ``assets.source`` value for rows this module creates.
UNIVERSE_SOURCE = "universe_index"


@dataclass(frozen=True)
class UniverseIngestionResult:
    added: int
    updated: int
    unchanged: int
    fetched: dict[str, int] = field(default_factory=dict)  # source_name -> count
    failed_sources: dict[str, str] = field(default_factory=dict)
    skipped_symbols: list[str] = field(default_factory=list)

    @property
    def total_constituents(self) -> int:
        return sum(self.fetched.values())


def ingest_universe(
    conn: duckdb.DuckDBPyConnection,
    sources: list[UniverseSource],
) -> UniverseIngestionResult:
    fetched: dict[str, int] = {}
    failed_sources: dict[str, str] = {}
    skipped_symbols: list[str] = []
    merged: dict[str, dict] = {}

    for source in sources:
        try:
            constituents = source.fetch_constituents()
        except Exception as exc:  # noqa: BLE001 - one bad source must not abort the rest
            failed_sources[source.source_name] = f"{type(exc).__name__}: {exc}"
            continue
        fetched[source.source_name] = len(constituents)
        for constituent in constituents:
            symbol = normalize_symbol(constituent.symbol)
            if not symbol:
                skipped_symbols.append(repr(constituent.symbol))
                continue
            entry = merged.setdefault(
                symbol, {"name": None, "sector": None, "industry": None, "indices": set()}
            )
            entry["name"] = entry["name"] or constituent.name
            entry["sector"] = entry["sector"] or constituent.sector
            entry["industry"] = entry["industry"] or constituent.industry
            if constituent.index_name:
                entry["indices"].add(constituent.index_name)

    repo = AssetRepository(conn)
    existing_by_symbol = {a.symbol.upper(): a for a in repo.list_active()}

    to_upsert: list[Asset] = []
    added = updated = unchanged = 0
    for symbol, entry in sorted(merged.items()):
        indices = sorted(entry["indices"])
        current = existing_by_symbol.get(symbol)
        if current is None:
            to_upsert.append(
                Asset(
                    asset_id=make_asset_id("US", "equity", symbol),
                    symbol=symbol,
                    name=entry["name"],
                    asset_type="equity",
                    country="US",
                    currency="USD",
                    sector=entry["sector"],
                    industry=entry["industry"],
                    source=UNIVERSE_SOURCE,
                    metadata={"indices": indices},
                )
            )
            added += 1
            continue

        metadata = dict(current.metadata)
        metadata["indices"] = indices
        candidate = replace(
            current,
            name=current.name or entry["name"],
            sector=current.sector or entry["sector"],
            industry=current.industry or entry["industry"],
            metadata=metadata,
        )
        if candidate == current:
            unchanged += 1
        else:
            to_upsert.append(candidate)
            updated += 1

    repo.upsert_many(to_upsert)
    return UniverseIngestionResult(
        added=added,
        updated=updated,
        unchanged=unchanged,
        fetched=fetched,
        failed_sources=failed_sources,
        skipped_symbols=skipped_symbols,
    )


def normalize_symbol(symbol: str) -> str:
    """Uppercase and map share-class dots to the yfinance dash form.

    Wikipedia lists ``BRK.B`` / ``BF.B``; the price source expects ``BRK-B``.
    Normalizing here keeps one registry row per security regardless of which
    path (universe ingest vs holdings resolver) registered it first.
    """
    return symbol.strip().upper().replace(".", "-")

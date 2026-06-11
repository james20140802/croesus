"""
One-time asset-type backfill (Sprint 008a).

Assets registered before the classifier existed carry the coarse provider type
("etf" for every ETF, "cryptocurrency" for crypto). This job reclassifies them
in place so policy sleeves (e.g. ``defensive_bonds`` matching ``bond_etf``)
start working for existing rows. Idempotent; only ``asset_type`` is updated —
``asset_id`` is a stable primary key and is never rewritten.

With ``--refresh-metadata``, ETFs lacking a yfinance ``category`` are re-fetched
first so the classifier has text to work with (network access required).
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from typing import Callable, Sequence

import duckdb

from croesus.assets.classifier import classify_asset_type
from croesus.assets.metadata_provider import AssetMetadataProvider
from croesus.assets.repository import AssetRepository
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def backfill_asset_types(
    conn: duckdb.DuckDBPyConnection,
    *,
    metadata_provider: AssetMetadataProvider | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, str]:
    """Reclassify active assets in place; returns {asset_id: new_type}."""
    repo = AssetRepository(conn)
    changed: dict[str, str] = {}

    for asset in repo.list_active():
        candidate = asset
        if (
            metadata_provider is not None
            and asset.asset_type == "etf"
            and not (asset.metadata or {}).get("category")
        ):
            fetched = metadata_provider.get_asset(asset.symbol)
            if fetched is not None and (fetched.metadata or {}).get("category"):
                merged = dict(asset.metadata or {})
                merged["category"] = fetched.metadata["category"]
                candidate = replace(asset, metadata=merged)
                repo.upsert_many([candidate])

        refined = classify_asset_type(candidate)
        if refined != asset.asset_type:
            conn.execute(
                "UPDATE assets SET asset_type = ? WHERE asset_id = ?",
                [refined, asset.asset_id],
            )
            changed[asset.asset_id] = refined
            log(f"reclassified {asset.asset_id}: {asset.asset_type} -> {refined}")

    if not changed:
        log("no assets needed reclassification")
    return changed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.backfill_asset_types",
        description=(
            "Reclassify existing asset rows with the refined type taxonomy "
            "(bond_etf / reit_etf / leveraged_etf / crypto). Idempotent."
        ),
    )
    parser.add_argument(
        "--refresh-metadata",
        action="store_true",
        help="re-fetch yfinance category for ETFs that lack one (network)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    provider = None
    if args.refresh_metadata:
        from croesus.data_sources.yfinance_metadata import YFinanceAssetMetadataProvider

        provider = YFinanceAssetMetadataProvider()

    migrate()
    with get_connection() as conn:
        changed = backfill_asset_types(conn, metadata_provider=provider)
    print(f"backfill complete: {len(changed)} asset(s) reclassified")


if __name__ == "__main__":
    main()

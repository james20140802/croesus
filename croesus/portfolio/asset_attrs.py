from __future__ import annotations

import json

import duckdb

from croesus.portfolio.models import AssetAttrs


def load_asset_attrs(
    conn: duckdb.DuckDBPyConnection, asset_ids: list[str]
) -> dict[str, AssetAttrs]:
    """Build classification attributes for assets from the ``assets`` table.

    Dedups and sorts ids for a stable query, skips synthetic ``CASH_*`` ids
    (the caller supplies cash attrs separately), and parses ``metadata.theme_tags``.
    """
    lookup = [a for a in sorted(set(asset_ids)) if not a.startswith("CASH_")]
    if not lookup:
        return {}
    placeholders = ", ".join("?" for _ in lookup)
    rows = conn.execute(
        f"""
        SELECT asset_id, asset_type, sector, industry, country, currency, name, metadata
        FROM assets
        WHERE asset_id IN ({placeholders})
        """,
        lookup,
    ).fetchall()
    attrs: dict[str, AssetAttrs] = {}
    for asset_id, asset_type, sector, industry, country, currency, name, metadata in rows:
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        attrs[asset_id] = AssetAttrs(
            asset_type=asset_type,
            sector=sector,
            industry=industry,
            country=country,
            currency=currency,
            theme_tags=list((metadata or {}).get("theme_tags") or []),
            name=name,
        )
    return attrs

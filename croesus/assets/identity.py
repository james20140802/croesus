"""
Stable asset-id construction (Sprint 008c).

``asset_id`` is the primary key every downstream table references, so the
construction rule lives in one place. Both the yfinance metadata provider and
the index-universe ingestion must mint identical ids for the same security,
otherwise one symbol would split into two registry rows.
"""
from __future__ import annotations

import re

_TYPE_PREFIXES = {
    "equity": "EQ",
    "etf": "ETF",
    "fund": "FUND",
}


def make_asset_id(country: str, asset_type: str, symbol: str) -> str:
    """Mint the canonical asset id, e.g. ``US_EQ_AAPL`` / ``US_EQ_BRK_B``."""
    type_prefix = _TYPE_PREFIXES.get(asset_type, asset_type.upper() or "ASSET")
    safe_symbol = re.sub(r"[^A-Z0-9]+", "_", symbol.upper()).strip("_")
    return f"{country}_{type_prefix}_{safe_symbol}"

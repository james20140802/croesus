"""
Asset-type refinement (Sprint 008a).

yfinance reports every ETF as ``quoteType=ETF``, but policy sleeves distinguish
``bond_etf`` / ``reit_etf`` / ``leveraged_etf`` — without refinement a bond ETF
is misfiled into the equity fallback sleeve and the ``defensive_bonds`` sleeve
stays permanently empty. Classification refines only the ``asset_type`` column;
``asset_id`` is a stable primary key and is never rewritten.
"""
from __future__ import annotations

from croesus.assets.models import Asset

# Asset types whose daily prices can be fetched and whose price-derived factors
# (momentum, volatility, liquidity, 200d MA) are meaningful. Shared by price
# ingestion and common-factor computation so the two can never drift apart.
# Cash and options are intentionally absent (no daily close series to fetch).
PRICEABLE_ASSET_TYPES = frozenset(
    {
        "equity",
        "etf",
        "bond_etf",
        "reit_etf",
        "reit",
        "leveraged_etf",
        "crypto",
        "fund",
    }
)

# Keyword sets are matched against name + yfinance category. Leveraged is
# checked first: "ProShares Ultra 20+ Year Treasury" must classify as
# leveraged_etf, not bond_etf.
_LEVERAGED_KEYWORDS = ("2x", "3x", "-1x", "ultra", "leveraged", "inverse", "daily bull", "daily bear")
_BOND_KEYWORDS = ("bond", "treasury", "fixed income", "aggregate", "municipal", "corporate debt")
_REIT_KEYWORDS = ("real estate", "reit")


def classify_asset_type(asset: Asset) -> str:
    """Return the refined asset_type for ``asset`` (may equal the current one)."""
    current = (asset.asset_type or "").lower()
    if current == "cryptocurrency":  # yfinance quoteType, normalized to the enum value
        return "crypto"
    if current != "etf":
        return current

    category = (asset.metadata or {}).get("category")
    text = " ".join(part for part in (asset.name, category) if part).lower()
    if any(keyword in text for keyword in _LEVERAGED_KEYWORDS):
        return "leveraged_etf"
    if any(keyword in text for keyword in _BOND_KEYWORDS):
        return "bond_etf"
    if any(keyword in text for keyword in _REIT_KEYWORDS):
        return "reit_etf"
    return "etf"

from __future__ import annotations

import re
from typing import Any

import yfinance as yf

from croesus.assets.models import Asset


class YFinanceAssetMetadataProvider:
    """Resolve asset metadata from yfinance behind the provider interface."""

    source_name = "yfinance_metadata"

    def get_asset(self, symbol: str) -> Asset | None:
        clean_symbol = symbol.strip().upper()
        if not clean_symbol:
            return None

        try:
            info = yf.Ticker(clean_symbol).get_info()
        except Exception:
            return None
        if not isinstance(info, dict) or not info:
            return None

        name = _first_text(info, "longName", "shortName", "displayName")
        if name is None:
            return None

        quote_type = str(info.get("quoteType") or "").upper()
        asset_type = _asset_type(quote_type)
        country = _country_code(info.get("country"))
        exchange = _first_text(info, "exchange", "fullExchangeName")
        currency = _first_text(info, "currency", "financialCurrency") or "USD"

        return Asset(
            asset_id=_asset_id(country, asset_type, clean_symbol),
            symbol=clean_symbol,
            name=name,
            asset_type=asset_type,
            country=country,
            exchange=exchange,
            currency=currency.upper(),
            sector=_first_text(info, "sector"),
            industry=_first_text(info, "industry"),
            source=self.source_name,
            metadata={
                "quote_type": quote_type or None,
                "market": _first_text(info, "market"),
                "category": _first_text(info, "category"),
            },
        )


def _first_text(info: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _asset_type(quote_type: str) -> str:
    if quote_type == "EQUITY":
        return "equity"
    if quote_type in {"ETF", "ETP"}:
        return "etf"
    if quote_type in {"MUTUALFUND", "FUND"}:
        return "fund"
    return quote_type.lower() if quote_type else "unknown"


def _country_code(country: Any) -> str:
    if not isinstance(country, str) or not country.strip():
        return "US"
    normalized = country.strip().upper()
    known = {
        "UNITED STATES": "US",
        "UNITED STATES OF AMERICA": "US",
        "SOUTH KOREA": "KR",
        "KOREA": "KR",
        "JAPAN": "JP",
        "CANADA": "CA",
        "UNITED KINGDOM": "GB",
    }
    return known.get(normalized, normalized[:2])


def _asset_id(country: str, asset_type: str, symbol: str) -> str:
    type_prefix = {
        "equity": "EQ",
        "etf": "ETF",
        "fund": "FUND",
    }.get(asset_type, asset_type.upper() or "ASSET")
    safe_symbol = re.sub(r"[^A-Z0-9]+", "_", symbol.upper()).strip("_")
    return f"{country}_{type_prefix}_{safe_symbol}"

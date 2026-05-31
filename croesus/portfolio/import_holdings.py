from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb

from croesus.portfolio.models import Holding

CASH_ASSET_ID = "CASH_USD"
_DEFAULT_PORTFOLIO_ID = "default"
_FALLBACK_CURRENCY = "USD"


@dataclass(frozen=True)
class HoldingsImport:
    """Outcome of parsing a holdings CSV: kept rows, skip count, and warnings."""

    holdings: list[Holding]
    warnings: list[str]
    skipped: int


def load_holdings_csv(
    path: str | Path,
    conn: duckdb.DuckDBPyConnection,
    as_of_date: date,
) -> HoldingsImport:
    """Parse a manual holdings CSV into validated :class:`Holding` rows.

    Rules (Level 1):
    - ``portfolio_id`` defaults to ``default`` when the column is absent/blank.
    - ``market_value`` is required; rows without it are skipped with a warning.
    - ``currency`` defaults to the active profile's base currency, else ``USD``.
    - Unknown ``asset_id`` is reported and skipped, unless it is ``CASH_USD``.

    Unknown or malformed rows never raise — they are skipped so the broader
    snapshot run survives partial input.
    """
    known_asset_ids = _known_asset_ids(conn)
    base_currency = _resolve_base_currency(conn)

    holdings: list[Holding] = []
    warnings: list[str] = []
    skipped = 0

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for line_no, row in enumerate(reader, start=2):  # line 1 is the header
            asset_id = _clean(row.get("asset_id"))
            if not asset_id:
                warnings.append(f"row {line_no}: missing asset_id, skipped")
                skipped += 1
                continue

            if asset_id != CASH_ASSET_ID and asset_id not in known_asset_ids:
                warnings.append(f"row {line_no}: unknown asset {asset_id}, skipped")
                skipped += 1
                continue

            market_value = _to_float(_clean(row.get("market_value")))
            if market_value is None:
                warnings.append(
                    f"row {line_no}: {asset_id} missing market_value, skipped"
                )
                skipped += 1
                continue

            portfolio_id = _clean(row.get("portfolio_id")) or _DEFAULT_PORTFOLIO_ID
            currency = _clean(row.get("currency")) or base_currency
            quantity = _to_float(_clean(row.get("quantity"))) or 0.0
            cost_basis = _to_float(_clean(row.get("cost_basis")))

            holdings.append(
                Holding(
                    portfolio_id=portfolio_id,
                    asset_id=asset_id,
                    as_of_date=as_of_date,
                    quantity=quantity,
                    market_value=market_value,
                    currency=currency,
                    cost_basis=cost_basis,
                    source="manual_csv",
                )
            )

    return HoldingsImport(holdings=holdings, warnings=warnings, skipped=skipped)


def _known_asset_ids(conn: duckdb.DuckDBPyConnection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT asset_id FROM assets").fetchall()}


def _resolve_base_currency(conn: duckdb.DuckDBPyConnection) -> str:
    """Best-effort base currency from the active profile, else ``USD``."""
    row = conn.execute(
        """
        SELECT base_currency FROM investor_profiles
        WHERE base_currency IS NOT NULL
        ORDER BY (profile_id = 'default') DESC, profile_id
        LIMIT 1
        """
    ).fetchone()
    if row and row[0]:
        return row[0]
    return _FALLBACK_CURRENCY


def _clean(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""


def _to_float(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None

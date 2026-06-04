from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb

from croesus.assets.metadata_provider import AssetMetadataProvider
from croesus.assets.resolver import AssetResolver
from croesus.data_sources.base import DailyPriceSource
from croesus.portfolio.models import Holding, ResolverStatus, is_cash

CASH_ASSET_ID = "CASH_USD"
_DEFAULT_PORTFOLIO_ID = "default"
_FALLBACK_CURRENCY = "USD"


@dataclass(frozen=True)
class HoldingsImport:
    """Outcome of parsing a holdings CSV: kept rows, skip count, and warnings."""

    holdings: list[Holding]
    warnings: list[str]
    skipped: int
    resolver_statuses: list[ResolverStatus] = field(default_factory=list)


def load_holdings_csv(
    path: str | Path,
    conn: duckdb.DuckDBPyConnection,
    as_of_date: date,
    *,
    portfolio_id: str = _DEFAULT_PORTFOLIO_ID,
    base_currency: str | None = None,
    metadata_provider: AssetMetadataProvider | None = None,
    price_source: DailyPriceSource | None = None,
) -> HoldingsImport:
    """Parse a manual holdings CSV into validated :class:`Holding` rows.

    ``portfolio_id`` is the portfolio being imported: rows that omit the
    ``portfolio_id`` column adopt it, and rows that explicitly name a *different*
    portfolio are skipped (counted + warned) since they belong to another book.
    ``base_currency`` is the governing profile's base currency, used to default
    rows that omit ``currency``; when not supplied it is resolved from the DB.

    Rules (Level 1):
    - ``portfolio_id`` defaults to the target portfolio when absent/blank.
    - ``market_value`` or ``quantity`` + ``avg_cost`` is required for securities.
    - ``currency`` defaults to the governing profile's base currency, else ``USD``.
    - Unknown ``asset_id`` is reported and skipped, unless it is ``CASH_<CUR>``.

    Unknown or malformed rows never raise — they are skipped so the broader
    snapshot run survives partial input.
    """
    known_asset_ids = _known_asset_ids(conn)
    resolver = AssetResolver(conn, metadata_provider, price_source)
    if base_currency is None:
        base_currency = _resolve_base_currency(conn)

    holdings: list[Holding] = []
    warnings: list[str] = []
    resolver_statuses: list[ResolverStatus] = []
    skipped = 0

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for line_no, row in enumerate(reader, start=2):  # line 1 is the header
            asset_id = _clean(row.get("asset_id"))
            symbol = _clean(row.get("symbol")).upper()
            if asset_id and symbol:
                expected_symbol = _symbol_for_asset_id(conn, asset_id)
                if expected_symbol is not None and expected_symbol.upper() != symbol:
                    message = (
                        f"symbol {symbol} does not match asset {asset_id} "
                        f"symbol {expected_symbol}"
                    )
                    warnings.append(f"row {line_no}: {message}")
                    resolver_statuses.append(
                        ResolverStatus(
                            row_number=line_no,
                            status="skipped",
                            symbol=symbol,
                            asset_id=asset_id,
                            message=message,
                        )
                    )
            elif symbol:
                resolution = resolver.resolve_symbol(symbol)
                asset_id = resolution.asset_id or ""
                if asset_id:
                    resolver_statuses.append(
                        ResolverStatus(
                            row_number=line_no,
                            status=resolution.status,
                            symbol=symbol,
                            asset_id=asset_id,
                            message=resolution.message,
                        )
                    )
                    known_asset_ids.add(asset_id)
                    if resolution.message and "failed" in resolution.message.lower():
                        warnings.append(f"row {line_no}: {resolution.message}")
                else:
                    message = resolution.message or "symbol unresolved"
                    warnings.append(f"row {line_no}: unresolved symbol {symbol}, skipped")
                    resolver_statuses.append(
                        ResolverStatus(
                            row_number=line_no,
                            status="unresolved",
                            symbol=symbol,
                            message=message,
                        )
                    )
                    skipped += 1
                    continue
            if not asset_id:
                warnings.append(f"row {line_no}: missing asset_id, skipped")
                skipped += 1
                continue

            row_portfolio_id = _clean(row.get("portfolio_id")) or portfolio_id
            if row_portfolio_id != portfolio_id:
                warnings.append(
                    f"row {line_no}: {asset_id} belongs to portfolio "
                    f"{row_portfolio_id!r}, not {portfolio_id!r}; skipped"
                )
                skipped += 1
                continue

            if not is_cash(asset_id) and asset_id not in known_asset_ids:
                warnings.append(f"row {line_no}: unknown asset {asset_id}, skipped")
                skipped += 1
                continue

            market_value = _to_float(_clean(row.get("market_value")))
            quantity = _to_float(_clean(row.get("quantity")))
            avg_cost = _to_float(_clean(row.get("avg_cost")))
            cost_basis = _to_float(_clean(row.get("cost_basis")))
            currency = (
                _clean(row.get("currency"))
                or _currency_from_cash(asset_id)
                or base_currency
            )

            if is_cash(asset_id) and market_value is None:
                warnings.append(
                    f"row {line_no}: {asset_id} missing market_value for cash, skipped"
                )
                skipped += 1
                continue

            if (
                not is_cash(asset_id)
                and market_value is None
                and (quantity is None or avg_cost is None)
            ):
                warnings.append(
                    f"row {line_no}: {asset_id} missing quantity/avg_cost or market_value, skipped"
                )
                skipped += 1
                continue

            holdings.append(
                Holding(
                    portfolio_id=portfolio_id,
                    asset_id=asset_id,
                    as_of_date=as_of_date,
                    quantity=quantity or 0.0,
                    market_value=market_value,
                    currency=currency,
                    cost_basis=cost_basis,
                    avg_cost=avg_cost,
                    source="manual_csv",
                )
            )

    return HoldingsImport(
        holdings=holdings,
        warnings=warnings,
        skipped=skipped,
        resolver_statuses=resolver_statuses,
    )


def _known_asset_ids(conn: duckdb.DuckDBPyConnection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT asset_id FROM assets").fetchall()}


def _symbol_for_asset_id(conn: duckdb.DuckDBPyConnection, asset_id: str) -> str | None:
    row = conn.execute(
        "SELECT symbol FROM assets WHERE asset_id = ? AND is_active = TRUE",
        [asset_id],
    ).fetchone()
    return row[0] if row else None


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


def _currency_from_cash(asset_id: str) -> str | None:
    if not is_cash(asset_id):
        return None
    currency = asset_id.removeprefix("CASH_").strip()
    return currency.upper() if currency else None

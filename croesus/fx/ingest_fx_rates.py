from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import duckdb
import pandas as pd

from croesus.data_sources.base import DailyPriceSource
from croesus.data_sources.yfinance_source import YFinanceDailyPriceSource
from croesus.fx.repository import FxRepository


@dataclass(frozen=True)
class FxIngestionResult:
    succeeded: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def ingest_fx_rates(
    conn: duckdb.DuckDBPyConnection,
    currencies: list[str],
    source: DailyPriceSource | None = None,
    *,
    period: str = "1y",
    log: Callable[[str], None] = print,
) -> FxIngestionResult:
    source = source or YFinanceDailyPriceSource()
    repo = FxRepository(conn)
    result = FxIngestionResult()

    for currency in sorted({_clean_currency(c) for c in currencies if c} - {"USD"}):
        symbol = f"{currency}=X"
        try:
            frame = source.fetch_daily_prices(symbol, period=period)
            if frame.empty:
                result.skipped[currency] = "no fx rows returned"
                log(f"skip {currency}: no fx rows returned")
                continue
            rows = repo.upsert_rates(
                currency,
                pd.DataFrame(
                    {
                        "date": frame["date"],
                        "rate_per_usd": frame["close"],
                    }
                ),
                source="yfinance",
            )
            result.succeeded.append(currency)
            log(f"stored {rows} FX rows for {currency}")
        except Exception as exc:  # noqa: BLE001 - per-currency failures must not stop the run.
            result.failed[currency] = str(exc)
            log(f"failed {currency}: {exc}")

    return result


def _clean_currency(currency: str) -> str:
    return currency.strip().upper()

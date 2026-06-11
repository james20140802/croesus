from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import duckdb
import pandas as pd

from croesus.assets.repository import AssetRepository
from croesus.data_sources.fundamentals.base import FundamentalsProvider
from croesus.data_sources.fundamentals.yfinance_fundamentals import (
    YFinanceFundamentalsProvider,
)
from croesus.fundamentals.repository import (
    METRIC_BOOK_VALUE_PER_SHARE,
    METRIC_SHARES_OUTSTANDING,
    METRIC_TOTAL_EQUITY,
    PERIOD_ANNUAL,
    PERIOD_QUARTERLY,
    FundamentalMetric,
    FundamentalsRepository,
)

# yfinance line-item labels mapped onto Croesus' metric vocabulary. Several
# labels can map to one metric — the first present in the statement index wins,
# so the list order is a priority order. Labels not listed here are ignored;
# metrics whose label is absent are simply not stored (NULL by omission), per
# the spec's "map explicitly, ignore the rest" rule.
_INCOME_LABELS: dict[str, list[str]] = {
    "revenue": ["Total Revenue"],
    "operating_income": ["Operating Income", "Operating Income Loss"],
    "net_income": [
        "Net Income",
        "Net Income Common Stockholders",
        "Net Income Continuous Operations",
    ],
    "eps": ["Diluted EPS", "Basic EPS"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
}
_BALANCE_LABELS: dict[str, list[str]] = {
    "total_debt": ["Total Debt"],
    "total_equity": [
        "Stockholders Equity",
        "Total Equity Gross Minority Interest",
        "Common Stock Equity",
    ],
    "cash_and_equivalents": [
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
    ],
    "shares_outstanding": ["Ordinary Shares Number", "Share Issued"],
}
_CASHFLOW_LABELS: dict[str, list[str]] = {
    "free_cash_flow": ["Free Cash Flow"],
    "capex": ["Capital Expenditure", "Capital Expenditures"],
}


@dataclass(frozen=True)
class FundamentalsIngestionResult:
    succeeded: dict[str, int] = field(default_factory=dict)  # symbol -> rows stored
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def ingest_fundamentals(
    conn: duckdb.DuckDBPyConnection,
    provider: FundamentalsProvider | None = None,
    *,
    log: Callable[[str], None] = print,
) -> FundamentalsIngestionResult:
    """Fetch, normalize, and store financial-statement metrics for US equities.

    Per-asset failures are logged and skipped so one bad symbol never stops the
    run. Returns a per-symbol summary.
    """
    provider = provider or YFinanceFundamentalsProvider()
    source = getattr(provider, "source_name", "fundamentals")
    assets = AssetRepository(conn).list_active(asset_type="equity", country="US")
    repo = FundamentalsRepository(conn)
    result = FundamentalsIngestionResult()

    for asset in assets:
        try:
            financials = provider.get_financials(asset.symbol)
            metrics = _normalize(asset.asset_id, financials, source=source)
            if not metrics:
                result.skipped[asset.symbol] = "no fundamentals returned"
                log(f"skip fundamentals for {asset.symbol}: no data returned")
                continue
            stored = repo.upsert_metrics(metrics)
            result.succeeded[asset.symbol] = stored
            log(f"stored {stored} fundamental metrics for {asset.symbol}")
        except Exception as exc:  # noqa: BLE001 - per-asset failures must not stop the run.
            result.failed[asset.symbol] = str(exc)
            log(f"failed fundamentals for {asset.symbol}: {exc}")

    return result


def _normalize(
    asset_id: str, financials: dict, *, source: str
) -> list[FundamentalMetric]:
    metrics: list[FundamentalMetric] = []
    metrics += _extract(
        asset_id, financials.get("income_annual"), _INCOME_LABELS, PERIOD_ANNUAL, source
    )
    metrics += _extract(
        asset_id,
        financials.get("income_quarterly"),
        _INCOME_LABELS,
        PERIOD_QUARTERLY,
        source,
    )
    balance = _extract(
        asset_id,
        financials.get("balance_annual"),
        _BALANCE_LABELS,
        PERIOD_ANNUAL,
        source,
    )
    metrics += balance
    metrics += _extract(
        asset_id,
        financials.get("cashflow_annual"),
        _CASHFLOW_LABELS,
        PERIOD_ANNUAL,
        source,
    )
    # book_value_per_share is not a reported line item — derive it from equity
    # and share count at each annual period_end where both are present.
    metrics += _derive_book_value_per_share(asset_id, balance, source=source)
    return metrics


def _extract(
    asset_id: str,
    frame: pd.DataFrame | None,
    label_map: dict[str, list[str]],
    period_type: str,
    source: str,
) -> list[FundamentalMetric]:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    out: list[FundamentalMetric] = []
    for metric_name, candidates in label_map.items():
        label = next((c for c in candidates if c in frame.index), None)
        if label is None:
            continue
        row = frame.loc[label]
        for column, raw in row.items():
            period_end = _to_date(column)
            if period_end is None or pd.isna(raw):
                continue
            out.append(
                FundamentalMetric(
                    asset_id=asset_id,
                    period_end=period_end,
                    period_type=period_type,
                    metric_name=metric_name,
                    value=float(raw),
                    source=source,
                )
            )
    return out


def _derive_book_value_per_share(
    asset_id: str, balance_metrics: list[FundamentalMetric], *, source: str
) -> list[FundamentalMetric]:
    equity = {
        m.period_end: m.value
        for m in balance_metrics
        if m.metric_name == METRIC_TOTAL_EQUITY and m.value is not None
    }
    shares = {
        m.period_end: m.value
        for m in balance_metrics
        if m.metric_name == METRIC_SHARES_OUTSTANDING and m.value is not None
    }
    out: list[FundamentalMetric] = []
    for period_end, equity_value in equity.items():
        share_count = shares.get(period_end)
        if not share_count:  # missing or zero shares -> cannot derive
            continue
        out.append(
            FundamentalMetric(
                asset_id=asset_id,
                period_end=period_end,
                period_type=PERIOD_ANNUAL,
                metric_name=METRIC_BOOK_VALUE_PER_SHARE,
                value=equity_value / share_count,
                source=source,
            )
        )
    return out


def _to_date(value) -> date | None:
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    return ts.date()

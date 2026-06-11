from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

# The stable metric vocabulary stored in the fundamentals table. These strings
# are a contract (primary-key components and downstream factor inputs) — keep
# them stable.
METRIC_REVENUE = "revenue"
METRIC_OPERATING_INCOME = "operating_income"
METRIC_NET_INCOME = "net_income"
METRIC_EPS = "eps"
METRIC_FREE_CASH_FLOW = "free_cash_flow"
METRIC_TOTAL_DEBT = "total_debt"
METRIC_TOTAL_EQUITY = "total_equity"
METRIC_CASH_AND_EQUIVALENTS = "cash_and_equivalents"
METRIC_SHARES_OUTSTANDING = "shares_outstanding"
METRIC_EBITDA = "ebitda"
METRIC_CAPEX = "capex"
METRIC_BOOK_VALUE_PER_SHARE = "book_value_per_share"

METRIC_NAMES = (
    METRIC_REVENUE,
    METRIC_OPERATING_INCOME,
    METRIC_NET_INCOME,
    METRIC_EPS,
    METRIC_FREE_CASH_FLOW,
    METRIC_TOTAL_DEBT,
    METRIC_TOTAL_EQUITY,
    METRIC_CASH_AND_EQUIVALENTS,
    METRIC_SHARES_OUTSTANDING,
    METRIC_EBITDA,
    METRIC_CAPEX,
    METRIC_BOOK_VALUE_PER_SHARE,
)

PERIOD_ANNUAL = "annual"
PERIOD_QUARTERLY = "quarterly"


@dataclass(frozen=True)
class FundamentalMetric:
    asset_id: str
    period_end: date
    period_type: str
    metric_name: str
    value: float | None
    source: str | None = None


class FundamentalsRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert_metrics(self, metrics: list[FundamentalMetric]) -> int:
        """Insert/replace metric rows; returns the number of rows written."""
        if not metrics:
            return 0
        self.conn.executemany(
            """
            INSERT INTO fundamentals (
              asset_id, period_end, period_type, metric_name, value, source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, period_end, period_type, metric_name)
            DO UPDATE SET value = excluded.value, source = excluded.source
            """,
            [
                (
                    m.asset_id,
                    m.period_end,
                    m.period_type,
                    m.metric_name,
                    m.value,
                    m.source,
                )
                for m in metrics
            ],
        )
        return len(metrics)

    def get_metric_series(
        self, asset_id: str, metric_name: str, *, period_type: str = PERIOD_ANNUAL
    ) -> list[tuple[date, float]]:
        """All non-NULL ``(period_end, value)`` for a metric, oldest first."""
        rows = self.conn.execute(
            """
            SELECT period_end, value
            FROM fundamentals
            WHERE asset_id = ? AND metric_name = ? AND period_type = ?
              AND value IS NOT NULL
            ORDER BY period_end
            """,
            [asset_id, metric_name, period_type],
        ).fetchall()
        return [(row[0], float(row[1])) for row in rows]

    def get_annual_fcf(self, asset_id: str) -> list[tuple[date, float]]:
        """Annual free-cash-flow history, oldest first (for DCF growth)."""
        return self.get_metric_series(
            asset_id, METRIC_FREE_CASH_FLOW, period_type=PERIOD_ANNUAL
        )

    def get_latest_metric(
        self, asset_id: str, metric_name: str, *, period_type: str = PERIOD_ANNUAL
    ) -> float | None:
        """Most recent non-NULL value of a metric, or ``None``."""
        series = self.get_metric_series(asset_id, metric_name, period_type=period_type)
        return series[-1][1] if series else None

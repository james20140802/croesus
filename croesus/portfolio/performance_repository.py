"""Persistence for performance/goal tracking (Sprint 006d).

:class:`PerformanceRepository` reads the portfolio snapshot history that the
return math folds over and writes the ``portfolio_performance_snapshots`` rows
that a dashboard or report reads back. Annualized return and the attribution
breakdown ride along in the ``metadata`` JSON column.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import Any

import duckdb

from croesus.portfolio.performance import Attribution, PerformancePeriod


class PerformanceRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    # -- snapshot history (input to the return math) ------------------------

    def get_snapshot_history(
        self, portfolio_id: str, *, up_to: date | None = None
    ) -> list[dict[str, Any]]:
        """Return this portfolio's snapshots oldest-first.

        Each row is a dict with at least ``as_of_date`` and
        ``total_market_value``. ``up_to`` caps the date inclusively.
        """
        sql = "SELECT * FROM portfolio_snapshots WHERE portfolio_id = ?"
        params: list[Any] = [portfolio_id]
        if up_to is not None:
            sql += " AND as_of_date <= ?"
            params.append(up_to)
        sql += " ORDER BY as_of_date"
        rows = self.conn.execute(sql, params).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(zip(columns, row))
            data["metadata"] = _to_dict(data.get("metadata"))
            out.append(data)
        return out

    # -- performance rows ---------------------------------------------------

    def save_period(self, period: PerformancePeriod) -> None:
        """Upsert one ``(portfolio, as_of_date, period)`` progress row."""
        self.conn.execute(
            """
            INSERT INTO portfolio_performance_snapshots (
              portfolio_id, as_of_date, period, start_value, end_value,
              net_contributions, investment_return, investment_return_pct,
              target_return_pct, return_gap_pct, max_drawdown_pct, risk_status,
              status, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)
            ON CONFLICT (portfolio_id, as_of_date, period) DO UPDATE SET
              start_value = excluded.start_value,
              end_value = excluded.end_value,
              net_contributions = excluded.net_contributions,
              investment_return = excluded.investment_return,
              investment_return_pct = excluded.investment_return_pct,
              target_return_pct = excluded.target_return_pct,
              return_gap_pct = excluded.return_gap_pct,
              max_drawdown_pct = excluded.max_drawdown_pct,
              risk_status = excluded.risk_status,
              status = excluded.status,
              metadata = excluded.metadata
            """,
            (
                period.portfolio_id,
                period.as_of_date,
                period.period,
                period.start_value,
                period.end_value,
                period.net_contributions,
                period.investment_return,
                period.investment_return_pct,
                period.target_return_pct,
                period.return_gap_pct,
                period.max_drawdown_pct,
                period.risk_status,
                period.status,
                json.dumps(_metadata_for(period)),
            ),
        )

    def save_periods(self, periods: list[PerformancePeriod]) -> None:
        for period in periods:
            self.save_period(period)

    def get_period(
        self, portfolio_id: str, as_of_date: date, period: str
    ) -> PerformancePeriod | None:
        row = self.conn.execute(
            """
            SELECT * FROM portfolio_performance_snapshots
            WHERE portfolio_id = ? AND as_of_date = ? AND period = ?
            """,
            [portfolio_id, as_of_date, period],
        ).fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in self.conn.description]
        return _row_to_period(dict(zip(columns, row)))

    def list_periods(
        self, portfolio_id: str, as_of_date: date
    ) -> list[PerformancePeriod]:
        rows = self.conn.execute(
            """
            SELECT * FROM portfolio_performance_snapshots
            WHERE portfolio_id = ? AND as_of_date = ?
            ORDER BY period
            """,
            [portfolio_id, as_of_date],
        ).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [_row_to_period(dict(zip(columns, row))) for row in rows]


def _metadata_for(period: PerformancePeriod) -> dict[str, Any]:
    """Pack annualized return + attribution into the JSON metadata column."""
    metadata = dict(period.metadata)
    metadata["annualized_return_pct"] = period.annualized_return_pct
    metadata["attribution"] = asdict(period.attribution)
    return metadata


def _row_to_period(row: dict[str, Any]) -> PerformancePeriod:
    metadata = _to_dict(row.get("metadata"))
    attribution = _attribution_from(metadata.get("attribution"))
    return PerformancePeriod(
        portfolio_id=row["portfolio_id"],
        as_of_date=row["as_of_date"],
        period=row["period"],
        start_value=row.get("start_value"),
        end_value=row.get("end_value"),
        net_contributions=row.get("net_contributions") or 0.0,
        investment_return=row.get("investment_return"),
        investment_return_pct=row.get("investment_return_pct"),
        annualized_return_pct=metadata.get("annualized_return_pct"),
        target_return_pct=row.get("target_return_pct"),
        return_gap_pct=row.get("return_gap_pct"),
        max_drawdown_pct=row.get("max_drawdown_pct"),
        risk_status=row["risk_status"],
        status=row["status"],
        attribution=attribution,
        metadata=metadata,
    )


def _attribution_from(value: Any) -> Attribution:
    data = value or {}
    return Attribution(
        net_contributions=data.get("net_contributions") or 0.0,
        realized=data.get("realized") or 0.0,
        dividends=data.get("dividends") or 0.0,
        market_movement=data.get("market_movement"),
        notes=list(data.get("notes") or []),
    )


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value or {}

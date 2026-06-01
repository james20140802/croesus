from __future__ import annotations

import json
from datetime import date
from typing import Any

import duckdb

from croesus.portfolio.models import Exposure, Holding, PolicyDrift, Portfolio


class PortfolioRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    # -- portfolios ---------------------------------------------------------

    def upsert_portfolio(self, portfolio: Portfolio) -> None:
        # now() (not CURRENT_TIMESTAMP) — the latter trips a DuckDB binder bug
        # when combined with ON CONFLICT DO UPDATE.
        self.conn.execute(
            """
            INSERT INTO portfolios (
              portfolio_id, profile_id, name, base_currency,
              created_at, updated_at, metadata
            )
            VALUES (?, ?, ?, ?, now(), now(), ?::JSON)
            ON CONFLICT (portfolio_id) DO UPDATE SET
              profile_id = excluded.profile_id,
              name = excluded.name,
              base_currency = excluded.base_currency,
              updated_at = now(),
              metadata = excluded.metadata
            """,
            (
                portfolio.portfolio_id,
                portfolio.profile_id,
                portfolio.name,
                portfolio.base_currency,
                json.dumps(portfolio.metadata),
            ),
        )

    def get_portfolio(self, portfolio_id: str) -> Portfolio | None:
        row = self.conn.execute(
            "SELECT * FROM portfolios WHERE portfolio_id = ?",
            [portfolio_id],
        ).fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in self.conn.description]
        data = dict(zip(columns, row))
        return Portfolio(
            portfolio_id=data["portfolio_id"],
            profile_id=data["profile_id"],
            name=data["name"],
            base_currency=data["base_currency"],
            metadata=_to_dict(data.get("metadata")),
        )

    # -- holdings -----------------------------------------------------------

    def replace_holdings(
        self, portfolio_id: str, as_of_date: date, holdings: list[Holding]
    ) -> None:
        """Make ``(portfolio_id, as_of_date)``'s holdings exactly ``holdings``.

        Deletes any prior rows for that date so a re-imported CSV does not
        leave stale positions behind. Atomic: the delete and inserts share one
        transaction.
        """
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                "DELETE FROM portfolio_holdings WHERE portfolio_id = ? AND as_of_date = ?",
                [portfolio_id, as_of_date],
            )
            if holdings:
                self.conn.executemany(
                    """
                    INSERT INTO portfolio_holdings (
                      portfolio_id, asset_id, as_of_date, quantity, market_value,
                      currency, cost_basis, source, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)
                    """,
                    [self._holding_to_params(h) for h in holdings],
                )
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def get_holdings(self, portfolio_id: str, as_of_date: date) -> list[Holding]:
        rows = self.conn.execute(
            """
            SELECT * FROM portfolio_holdings
            WHERE portfolio_id = ? AND as_of_date = ?
            ORDER BY asset_id
            """,
            [portfolio_id, as_of_date],
        ).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [self._row_to_holding(dict(zip(columns, row))) for row in rows]

    # -- snapshots ----------------------------------------------------------

    def save_snapshot(
        self,
        portfolio_id: str,
        as_of_date: date,
        total_market_value: float,
        *,
        cash_value: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO portfolio_snapshots (
              portfolio_id, as_of_date, total_market_value, cash_value, metadata
            )
            VALUES (?, ?, ?, ?, ?::JSON)
            ON CONFLICT (portfolio_id, as_of_date) DO UPDATE SET
              total_market_value = excluded.total_market_value,
              cash_value = excluded.cash_value,
              metadata = excluded.metadata
            """,
            (
                portfolio_id,
                as_of_date,
                total_market_value,
                cash_value,
                json.dumps(metadata or {}),
            ),
        )

    def get_snapshot(self, portfolio_id: str, as_of_date: date) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE portfolio_id = ? AND as_of_date = ?",
            [portfolio_id, as_of_date],
        ).fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in self.conn.description]
        data = dict(zip(columns, row))
        data["metadata"] = _to_dict(data.get("metadata"))
        return data

    # -- exposures ----------------------------------------------------------

    def replace_exposures(
        self, portfolio_id: str, as_of_date: date, exposures: list[Exposure]
    ) -> None:
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                "DELETE FROM portfolio_exposures WHERE portfolio_id = ? AND as_of_date = ?",
                [portfolio_id, as_of_date],
            )
            if exposures:
                self.conn.executemany(
                    """
                    INSERT INTO portfolio_exposures (
                      portfolio_id, as_of_date, exposure_type, exposure_name,
                      weight, market_value, limit_weight, is_violation
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            e.portfolio_id,
                            e.as_of_date,
                            e.exposure_type,
                            e.exposure_name,
                            e.weight,
                            e.market_value,
                            e.limit_weight,
                            e.is_violation,
                        )
                        for e in exposures
                    ],
                )
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def get_exposures(self, portfolio_id: str, as_of_date: date) -> list[Exposure]:
        rows = self.conn.execute(
            """
            SELECT * FROM portfolio_exposures
            WHERE portfolio_id = ? AND as_of_date = ?
            ORDER BY exposure_type, exposure_name
            """,
            [portfolio_id, as_of_date],
        ).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [
            Exposure(
                portfolio_id=d["portfolio_id"],
                as_of_date=d["as_of_date"],
                exposure_type=d["exposure_type"],
                exposure_name=d["exposure_name"],
                weight=d["weight"],
                market_value=d["market_value"],
                limit_weight=d["limit_weight"],
                is_violation=bool(d["is_violation"]),
            )
            for d in (dict(zip(columns, row)) for row in rows)
        ]

    # -- policy drifts ------------------------------------------------------

    def replace_drifts(
        self, portfolio_id: str, as_of_date: date, drifts: list[PolicyDrift]
    ) -> None:
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                "DELETE FROM policy_drifts WHERE portfolio_id = ? AND as_of_date = ?",
                [portfolio_id, as_of_date],
            )
            if drifts:
                self.conn.executemany(
                    """
                    INSERT INTO policy_drifts (
                      portfolio_id, as_of_date, sleeve_name, current_weight,
                      target_weight, min_weight, max_weight, drift, is_outside_band
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            d.portfolio_id,
                            d.as_of_date,
                            d.sleeve_name,
                            d.current_weight,
                            d.target_weight,
                            d.min_weight,
                            d.max_weight,
                            d.drift,
                            d.is_outside_band,
                        )
                        for d in drifts
                    ],
                )
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def get_drifts(self, portfolio_id: str, as_of_date: date) -> list[PolicyDrift]:
        rows = self.conn.execute(
            """
            SELECT * FROM policy_drifts
            WHERE portfolio_id = ? AND as_of_date = ?
            ORDER BY sleeve_name
            """,
            [portfolio_id, as_of_date],
        ).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [
            PolicyDrift(
                portfolio_id=d["portfolio_id"],
                as_of_date=d["as_of_date"],
                sleeve_name=d["sleeve_name"],
                current_weight=d["current_weight"],
                target_weight=d["target_weight"],
                min_weight=d["min_weight"],
                max_weight=d["max_weight"],
                drift=d["drift"],
                is_outside_band=bool(d["is_outside_band"]),
            )
            for d in (dict(zip(columns, row)) for row in rows)
        ]

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _holding_to_params(holding: Holding) -> tuple[Any, ...]:
        return (
            holding.portfolio_id,
            holding.asset_id,
            holding.as_of_date,
            holding.quantity,
            holding.market_value,
            holding.currency,
            holding.cost_basis,
            holding.source,
            json.dumps(holding.metadata),
        )

    @staticmethod
    def _row_to_holding(row: dict[str, Any]) -> Holding:
        return Holding(
            portfolio_id=row["portfolio_id"],
            asset_id=row["asset_id"],
            as_of_date=row["as_of_date"],
            quantity=row["quantity"],
            market_value=row["market_value"],
            currency=row["currency"],
            cost_basis=row["cost_basis"],
            source=row["source"],
            metadata=_to_dict(row.get("metadata")),
        )


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value or {}

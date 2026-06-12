from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

import duckdb

from croesus.portfolio.actions import (
    APPROVAL_PENDING,
    APPROVAL_TTL_DAYS,
    ProposedAction,
)
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
                      currency, cost_basis, avg_cost, source, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)
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
        total_cost_basis: float | None = None,
        unrealized_pnl: float | None = None,
        cash_value: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO portfolio_snapshots (
              portfolio_id, as_of_date, total_market_value, total_cost_basis,
              unrealized_pnl, cash_value, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?::JSON)
            ON CONFLICT (portfolio_id, as_of_date) DO UPDATE SET
              total_market_value = excluded.total_market_value,
              total_cost_basis = excluded.total_cost_basis,
              unrealized_pnl = excluded.unrealized_pnl,
              cash_value = excluded.cash_value,
              metadata = excluded.metadata
            """,
            (
                portfolio_id,
                as_of_date,
                total_market_value,
                total_cost_basis,
                unrealized_pnl,
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

    # -- rebalance runs -----------------------------------------------------

    def upsert_rebalance_run(
        self,
        run_id: str,
        portfolio_id: str,
        profile_id: str,
        as_of_date: date,
        *,
        decision: str,
        summary: str,
        macro_regime: str | None = None,
        macro_positioning: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO rebalance_runs (
              run_id, portfolio_id, profile_id, date, macro_regime,
              macro_positioning, decision, summary, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)
            ON CONFLICT (run_id) DO UPDATE SET
              portfolio_id = excluded.portfolio_id,
              profile_id = excluded.profile_id,
              date = excluded.date,
              macro_regime = excluded.macro_regime,
              macro_positioning = excluded.macro_positioning,
              decision = excluded.decision,
              summary = excluded.summary,
              metadata = excluded.metadata
            """,
            (
                run_id,
                portfolio_id,
                profile_id,
                as_of_date,
                macro_regime,
                macro_positioning,
                decision,
                summary,
                json.dumps(metadata or {}),
            ),
        )

    def replace_proposed_actions(
        self,
        run_id: str,
        actions: list[ProposedAction],
        *,
        now: datetime | None = None,
    ) -> None:
        """Replace this run's actions; other runs (and their approvals) are untouched.

        Approval gate invariant (Sprint 011): every persisted action that
        requires user approval carries a 'pending' status and a 7-day expiry,
        stamped here so no caller can persist an approvable action without an
        approval record. Actions already carrying approval state keep it.
        """
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute("DELETE FROM proposed_actions WHERE run_id = ?", [run_id])
            if actions:
                self.conn.executemany(
                    """
                    INSERT INTO proposed_actions (
                      action_id, run_id, asset_id, sleeve_name, action_type,
                      current_weight, target_weight, proposed_weight,
                      estimated_trade_value, reason_codes, human_readable_reason,
                      requires_research, requires_user_approval,
                      approval_status, approved_at, approval_notes, expires_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        self._action_to_params(self._with_approval_default(action, now))
                        for action in actions
                    ],
                )
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    @staticmethod
    def _with_approval_default(
        action: ProposedAction, now: datetime | None
    ) -> ProposedAction:
        if not action.requires_user_approval or action.approval_status is not None:
            return action
        stamp = now or datetime.now(timezone.utc)
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
        return replace(
            action,
            approval_status=APPROVAL_PENDING,
            expires_at=stamp + timedelta(days=APPROVAL_TTL_DAYS),
        )

    def get_rebalance_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM rebalance_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in self.conn.description]
        data = dict(zip(columns, row))
        data["metadata"] = _to_dict(data.get("metadata"))
        data["actions"] = self.list_proposed_actions(run_id)
        return data

    def load_latest_rebalance_run(self, portfolio_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT run_id
            FROM rebalance_runs
            WHERE portfolio_id = ?
            ORDER BY date DESC, run_id DESC
            LIMIT 1
            """,
            [portfolio_id],
        ).fetchone()
        return self.get_rebalance_run(row[0]) if row else None

    def list_proposed_actions(self, run_id: str) -> list[ProposedAction]:
        rows = self.conn.execute(
            """
            SELECT * FROM proposed_actions
            WHERE run_id = ?
            ORDER BY action_id
            """,
            [run_id],
        ).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [
            self._row_to_action(dict(zip(columns, row)))
            for row in rows
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
            holding.avg_cost,
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
            avg_cost=row.get("avg_cost"),
            source=row["source"],
            metadata=_to_dict(row.get("metadata")),
        )

    @staticmethod
    def _action_to_params(action: ProposedAction) -> tuple[Any, ...]:
        return (
            action.action_id,
            action.run_id,
            action.asset_id,
            action.sleeve_name,
            action.action_type,
            action.current_weight,
            action.target_weight,
            action.proposed_weight,
            action.estimated_trade_value,
            json.dumps(action.reason_codes),
            action.human_readable_reason,
            action.requires_research,
            action.requires_user_approval,
            action.approval_status,
            action.approved_at,
            action.approval_notes,
            action.expires_at,
        )

    @staticmethod
    def _row_to_action(row: dict[str, Any]) -> ProposedAction:
        return ProposedAction(
            action_id=row["action_id"],
            run_id=row["run_id"],
            asset_id=row["asset_id"],
            sleeve_name=row["sleeve_name"],
            action_type=row["action_type"],
            current_weight=row["current_weight"],
            target_weight=row["target_weight"],
            proposed_weight=row["proposed_weight"],
            estimated_trade_value=row["estimated_trade_value"],
            reason_codes=_to_list(row.get("reason_codes")),
            human_readable_reason=row["human_readable_reason"],
            requires_research=bool(row["requires_research"]),
            requires_user_approval=bool(row["requires_user_approval"]),
            approval_status=row.get("approval_status"),
            approved_at=row.get("approved_at"),
            approval_notes=row.get("approval_notes"),
            expires_at=row.get("expires_at"),
        )


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value or {}


def _to_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return list(value or [])

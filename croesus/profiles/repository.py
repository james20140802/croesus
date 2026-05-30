from __future__ import annotations

import json
from typing import Any

import duckdb

from croesus.profiles.models import (
    AssetType,
    Currency,
    InvestorProfile,
    PolicyTarget,
    TradeMode,
)

_PROFILE_TIMESTAMP_COLUMNS = ("created_at", "updated_at")


class ProfileRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert_profile(self, profile: InvestorProfile) -> None:
        self.conn.execute(
            """
            INSERT INTO investor_profiles (
              profile_id, name, base_currency, expected_annual_return,
              max_tolerable_drawdown, investment_horizon_years, monthly_contribution,
              liquidity_buffer_months, allowed_asset_types, disallowed_asset_types,
              max_single_position_weight, max_sector_weight, max_industry_weight,
              max_theme_weight, max_country_weight, max_currency_weight,
              max_monthly_turnover, rebalance_band, trade_mode,
              created_at, updated_at, metadata
            )
            VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON, ?::JSON,
              ?, ?, ?, ?, ?, ?, ?, ?, ?,
              now(), now(), ?::JSON
            )
            ON CONFLICT (profile_id) DO UPDATE SET
              name = excluded.name,
              base_currency = excluded.base_currency,
              expected_annual_return = excluded.expected_annual_return,
              max_tolerable_drawdown = excluded.max_tolerable_drawdown,
              investment_horizon_years = excluded.investment_horizon_years,
              monthly_contribution = excluded.monthly_contribution,
              liquidity_buffer_months = excluded.liquidity_buffer_months,
              allowed_asset_types = excluded.allowed_asset_types,
              disallowed_asset_types = excluded.disallowed_asset_types,
              max_single_position_weight = excluded.max_single_position_weight,
              max_sector_weight = excluded.max_sector_weight,
              max_industry_weight = excluded.max_industry_weight,
              max_theme_weight = excluded.max_theme_weight,
              max_country_weight = excluded.max_country_weight,
              max_currency_weight = excluded.max_currency_weight,
              max_monthly_turnover = excluded.max_monthly_turnover,
              rebalance_band = excluded.rebalance_band,
              trade_mode = excluded.trade_mode,
              updated_at = now(),
              metadata = excluded.metadata
            """,
            self._profile_to_params(profile),
        )

    def save_profile(self, profile: InvestorProfile, targets: list[PolicyTarget]) -> None:
        """Persist a profile and its complete policy-target set atomically.

        The profile upsert, removal of stale sleeves, and new target inserts
        share one transaction, so a failure leaves the prior state intact
        rather than a half-applied "new profile with old targets".
        """
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.upsert_profile(profile)
            self.conn.execute(
                "DELETE FROM policy_targets WHERE profile_id = ?", [profile.profile_id]
            )
            self.upsert_policy_targets(targets)
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def get_profile(self, profile_id: str) -> InvestorProfile | None:
        row = self.conn.execute(
            "SELECT * FROM investor_profiles WHERE profile_id = ?",
            [profile_id],
        ).fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in self.conn.description]
        return self._row_to_profile(dict(zip(columns, row)))

    def upsert_policy_targets(self, targets: list[PolicyTarget]) -> None:
        if not targets:
            return
        self.conn.executemany(
            """
            INSERT INTO policy_targets (
              profile_id, sleeve_name, target_weight, min_weight, max_weight, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?::JSON)
            ON CONFLICT (profile_id, sleeve_name) DO UPDATE SET
              target_weight = excluded.target_weight,
              min_weight = excluded.min_weight,
              max_weight = excluded.max_weight,
              metadata = excluded.metadata
            """,
            [self._target_to_params(target) for target in targets],
        )

    def replace_policy_targets(self, profile_id: str, targets: list[PolicyTarget]) -> None:
        """Make ``profile_id``'s policy targets exactly ``targets``.

        Deletes any sleeves no longer present so a reloaded config does not
        leave stale rows behind. Atomic: the delete and inserts share one
        transaction.
        """
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                "DELETE FROM policy_targets WHERE profile_id = ?", [profile_id]
            )
            self.upsert_policy_targets(targets)
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def get_policy_targets(self, profile_id: str) -> list[PolicyTarget]:
        rows = self.conn.execute(
            "SELECT * FROM policy_targets WHERE profile_id = ? ORDER BY sleeve_name",
            [profile_id],
        ).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [self._row_to_target(dict(zip(columns, row))) for row in rows]

    @staticmethod
    def _profile_to_params(profile: InvestorProfile) -> tuple[Any, ...]:
        return (
            profile.profile_id,
            profile.name,
            profile.base_currency.value,
            profile.expected_annual_return,
            profile.max_tolerable_drawdown,
            profile.investment_horizon_years,
            profile.monthly_contribution,
            profile.liquidity_buffer_months,
            json.dumps([t.value for t in profile.allowed_asset_types]),
            json.dumps([t.value for t in profile.disallowed_asset_types]),
            profile.max_single_position_weight,
            profile.max_sector_weight,
            profile.max_industry_weight,
            profile.max_theme_weight,
            profile.max_country_weight,
            profile.max_currency_weight,
            profile.max_monthly_turnover,
            profile.rebalance_band,
            profile.trade_mode.value,
            json.dumps(profile.metadata),
        )

    @staticmethod
    def _row_to_profile(row: dict[str, Any]) -> InvestorProfile:
        return InvestorProfile(
            profile_id=row["profile_id"],
            name=row["name"],
            base_currency=Currency(row["base_currency"]),
            expected_annual_return=row["expected_annual_return"],
            max_tolerable_drawdown=row["max_tolerable_drawdown"],
            investment_horizon_years=int(row["investment_horizon_years"]),
            monthly_contribution=row["monthly_contribution"],
            liquidity_buffer_months=row["liquidity_buffer_months"],
            allowed_asset_types=_to_asset_types(row["allowed_asset_types"]),
            disallowed_asset_types=_to_asset_types(row["disallowed_asset_types"]),
            max_single_position_weight=row["max_single_position_weight"],
            max_sector_weight=row["max_sector_weight"],
            max_industry_weight=row["max_industry_weight"],
            max_theme_weight=row["max_theme_weight"],
            max_country_weight=row["max_country_weight"],
            max_currency_weight=row["max_currency_weight"],
            max_monthly_turnover=row["max_monthly_turnover"],
            rebalance_band=row["rebalance_band"],
            trade_mode=TradeMode(row["trade_mode"]),
            metadata=_to_dict(row.get("metadata")),
        )

    @staticmethod
    def _target_to_params(target: PolicyTarget) -> tuple[Any, ...]:
        return (
            target.profile_id,
            target.sleeve_name,
            target.target_weight,
            target.min_weight,
            target.max_weight,
            json.dumps(target.metadata),
        )

    @staticmethod
    def _row_to_target(row: dict[str, Any]) -> PolicyTarget:
        return PolicyTarget(
            profile_id=row["profile_id"],
            sleeve_name=row["sleeve_name"],
            target_weight=row["target_weight"],
            min_weight=row["min_weight"],
            max_weight=row["max_weight"],
            metadata=_to_dict(row.get("metadata")),
        )


def _to_asset_types(value: Any) -> list[AssetType]:
    if isinstance(value, str):
        value = json.loads(value)
    return [AssetType(item) for item in (value or [])]


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value or {}

"""Phase E opportunity risk gate (recommendation-only).

Checks a user-selected opportunity candidate — a *prospective new buy*, not a
holding — against the existing portfolio risk gate: concentration capacity
(``block_new_buy`` semantics), asset-type eligibility, and a liquidity floor.
Produces a ``pass``/``warn``/``block`` verdict per candidate. It never proposes
trades, re-ranks, or writes to the portfolio layer; the human owns the decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

import duckdb

from croesus.portfolio.asset_attrs import load_asset_attrs
from croesus.portfolio.exposure import ExposureLimits, compute_exposures
from croesus.portfolio.models import AssetAttrs, Exposure
from croesus.portfolio.repository import PortfolioRepository
from croesus.profiles.models import InvestorProfile
from croesus.profiles.repository import ProfileRepository

DEFAULT_MIN_LIQUIDITY_USD = 1_000_000

_BUCKET_REASON = {
    "sector": "SECTOR_OVER_MAX",
    "industry": "INDUSTRY_OVER_MAX",
    "country": "COUNTRY_OVER_MAX",
    "currency": "CURRENCY_OVER_MAX",
}


@dataclass(frozen=True)
class RiskGateVerdict:
    status: str  # 'pass' | 'warn' | 'block'
    reason_codes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _candidate_bucket_name(candidate: AssetAttrs, exposure_type: str) -> str | None:
    if exposure_type == "sector":
        return candidate.sector or "Unknown"
    if exposure_type == "industry":
        return candidate.industry or "Unknown"
    if exposure_type == "country":
        return candidate.country or "Unknown"
    if exposure_type == "currency":
        return candidate.currency or "Unknown"
    return None


def evaluate_risk_gate(
    candidate_asset_id: str,
    candidate: AssetAttrs,
    *,
    exposures: list[Exposure],
    held_asset_ids: set[str],
    profile: InvestorProfile,
    liquidity_value: float | None,
    min_liquidity_usd: float | None,
) -> RiskGateVerdict:
    """Decide pass/warn/block for one prospective-buy candidate. Pure."""
    block_codes: list[str] = []
    warn_codes: list[str] = []
    notes: list[str] = []

    for exp in exposures:
        if not exp.is_violation:
            continue
        if exp.exposure_type == "position":
            if exp.exposure_name == candidate_asset_id:
                block_codes.append("POSITION_OVER_MAX")
                notes.append(
                    f"POSITION_OVER_MAX: {candidate_asset_id} weight "
                    f"{exp.weight:.1%} > cap {exp.limit_weight:.1%}"
                )
            continue
        reason = _BUCKET_REASON.get(exp.exposure_type)
        if reason is None:
            continue
        if exp.exposure_name == _candidate_bucket_name(candidate, exp.exposure_type):
            block_codes.append(reason)
            notes.append(
                f"{reason}: {exp.exposure_type} '{exp.exposure_name}' "
                f"{exp.weight:.1%} > cap {exp.limit_weight:.1%} (no room for new buy)"
            )

    disallowed = {t.value for t in profile.disallowed_asset_types}
    allowed = {t.value for t in profile.allowed_asset_types}
    atype = candidate.asset_type
    if atype is not None and (
        atype in disallowed or (allowed and atype not in allowed)
    ):
        block_codes.append("DISALLOWED_ASSET_TYPE")
        notes.append(
            f"DISALLOWED_ASSET_TYPE: asset_type '{atype}' not permitted by profile"
        )

    if min_liquidity_usd and (
        liquidity_value is None or liquidity_value < min_liquidity_usd
    ):
        warn_codes.append("LIQUIDITY_BELOW_MINIMUM")
        shown = "n/a" if liquidity_value is None else f"${liquidity_value:,.0f}"
        notes.append(
            f"LIQUIDITY_BELOW_MINIMUM: liquidity_1m {shown} "
            f"< floor ${min_liquidity_usd:,.0f}"
        )

    if candidate_asset_id in held_asset_ids:
        notes.append(f"ALREADY_HELD: {candidate_asset_id} is in the current portfolio")

    if block_codes:
        status = "block"
    elif warn_codes:
        status = "warn"
    else:
        status = "pass"
    return RiskGateVerdict(
        status=status, reason_codes=[*block_codes, *warn_codes], notes=notes
    )


def _latest_snapshot_date(
    conn: duckdb.DuckDBPyConnection, portfolio_id: str
) -> date | None:
    row = conn.execute(
        """
        SELECT as_of_date FROM portfolio_snapshots
        WHERE portfolio_id = ? ORDER BY as_of_date DESC LIMIT 1
        """,
        [portfolio_id],
    ).fetchone()
    return row[0] if row else None


def _load_liquidity(
    conn: duckdb.DuckDBPyConnection,
    asset_ids: Sequence[str],
    as_of_date: date,
) -> dict[str, float]:
    ids = list(dict.fromkeys(asset_ids))
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT asset_id, value,
                   ROW_NUMBER() OVER (PARTITION BY asset_id ORDER BY date DESC) AS rn
            FROM factor_values
            WHERE factor_name = 'liquidity_1m'
              AND date <= ?
              AND asset_id IN ({placeholders})
        )
        SELECT asset_id, value FROM ranked WHERE rn = 1
        """,
        [as_of_date, *ids],
    ).fetchall()
    return {asset_id: value for asset_id, value in rows}


def evaluate_candidates(
    conn: duckdb.DuckDBPyConnection,
    candidate_asset_ids: Sequence[str],
    *,
    portfolio_id: str,
    profile_id: str,
    as_of_date: date,
    min_liquidity_usd: float | None = DEFAULT_MIN_LIQUIDITY_USD,
) -> dict[str, RiskGateVerdict]:
    """Gather portfolio/profile/liquidity inputs and verdict each candidate.

    Returns ``{}`` when the profile is missing so the caller can leave cards
    ungated. An absent snapshot yields no holdings -> eligibility-only verdicts.
    """
    profile = ProfileRepository(conn).get_profile(profile_id)
    if profile is None:
        return {}

    snapshot_date = as_of_date or _latest_snapshot_date(conn, portfolio_id)
    portfolio_repo = PortfolioRepository(conn)
    holdings = (
        portfolio_repo.get_holdings(portfolio_id, snapshot_date)
        if snapshot_date is not None
        else []
    )
    held = {h.asset_id for h in holdings}
    attrs = load_asset_attrs(
        conn, [h.asset_id for h in holdings] + list(candidate_asset_ids)
    )
    limits = ExposureLimits(
        max_single_position_weight=profile.max_single_position_weight,
        max_sector_weight=profile.max_sector_weight,
        max_industry_weight=profile.max_industry_weight,
        max_theme_weight=profile.max_theme_weight,
        max_country_weight=profile.max_country_weight,
        max_currency_weight=profile.max_currency_weight,
    )
    exposures = compute_exposures(
        holdings, attrs, limits,
        portfolio_id=portfolio_id,
        as_of_date=snapshot_date or as_of_date,
    )
    liquidity = _load_liquidity(conn, candidate_asset_ids, as_of_date)

    return {
        asset_id: evaluate_risk_gate(
            asset_id,
            attrs.get(asset_id, AssetAttrs()),
            exposures=exposures,
            held_asset_ids=held,
            profile=profile,
            liquidity_value=liquidity.get(asset_id),
            min_liquidity_usd=min_liquidity_usd,
        )
        for asset_id in candidate_asset_ids
    }

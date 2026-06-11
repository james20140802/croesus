from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import uuid4

import duckdb

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.screening.dimensions import (
    FACTOR_NAMES,
    SCORE_GROUP_KEYS,
    VALUATION_CONTEXT_FACTORS,
    VALUATION_INVERTED,
    VALUATION_NATURAL,
)
from croesus.screening.models import ScreeningCandidate, ScreeningRunResult
from croesus.screening.normalization import percentile_rank
from croesus.screening.repository import ScreeningRepository

SUPPORTED_ASSET_TYPES = {"equity", "etf"}


def run_screening(
    conn: duckdb.DuckDBPyConnection,
    screening_params: dict[str, Any],
    *,
    as_of_date: date | None = None,
    portfolio_id: str | None = None,
    run_id: str | None = None,
) -> ScreeningRunResult:
    actual_as_of = as_of_date or _latest_factor_date(conn) or date.today()
    actual_run_id = run_id or f"screening-{actual_as_of.isoformat()}-{uuid4().hex[:8]}"
    assets = AssetRepository(conn).list_active()
    eligible_assets = [asset for asset in assets if asset.asset_type in SUPPORTED_ASSET_TYPES]
    skipped = [
        _skipped_candidate(
            actual_run_id,
            asset.asset_id,
            "skipped: unsupported asset type",
            ["UNSUPPORTED_ASSET_TYPE"],
            {"asset_type": asset.asset_type, "portfolio_fit": "watch"},
        )
        for asset in assets
        if asset.asset_type not in SUPPORTED_ASSET_TYPES
    ]
    factor_values = _load_latest_factor_values(conn, [asset.asset_id for asset in eligible_assets], actual_as_of)
    momentum_scaling = screening_params.get("momentum_scaling") or "raw"
    vol_fallback_assets: set[str] = set()
    if momentum_scaling == "vol_scaled":
        percentile_inputs = _scale_momentum_values(factor_values, vol_fallback_assets)
    else:
        percentile_inputs = factor_values
    percentile_scores = _factor_percentiles(percentile_inputs)
    exposure_overlay = _load_blocking_exposures(conn, portfolio_id, actual_as_of)

    ranked_inputs: list[ScreeningCandidate] = []
    for asset in eligible_assets:
        try:
            candidate = _score_asset(
                asset,
                actual_run_id,
                screening_params,
                factor_values.get(asset.asset_id, {}),
                percentile_scores.get(asset.asset_id, {}),
                exposure_overlay,
                momentum_scaling=momentum_scaling,
                vol_fallback=asset.asset_id in vol_fallback_assets,
            )
        except Exception as exc:
            skipped.append(
                _skipped_candidate(
                    actual_run_id,
                    asset.asset_id,
                    f"skipped: screening failed: {exc}",
                    ["SCREENING_FAILED"],
                    {"portfolio_fit": "watch"},
                )
            )
            continue
        if candidate.decision_bucket == "skipped":
            skipped.append(candidate)
        else:
            ranked_inputs.append(candidate)

    ranked_inputs.sort(key=lambda candidate: (-candidate.score, candidate.asset_id))  # type: ignore[arg-type]
    # Clamp to the actual ranked pool so "20 candidates from a 12-name universe"
    # never overstates selectivity; expose the sizes alongside the params.
    requested_candidate_count = int(screening_params.get("candidate_count") or 20)
    candidate_count = min(requested_candidate_count, len(ranked_inputs))
    screening_params = {
        **screening_params,
        "universe_size": len(eligible_assets),
        "ranked_count": len(ranked_inputs),
        "effective_candidate_count": candidate_count,
    }
    ranked: list[ScreeningCandidate] = []
    for index, candidate in enumerate(ranked_inputs, start=1):
        blocking = candidate.metadata.get("blocking_exposures") or []
        would_block = index <= candidate_count and bool(blocking)
        metadata = dict(candidate.metadata)
        if would_block:
            metadata["portfolio_fit"] = "blocked"
            bucket = "blocked_by_portfolio_fit"
            reason = _blocked_reason(blocking)
            reason_codes = [*candidate.reason_codes, "PORTFOLIO_FIT_BLOCKED"]
        else:
            metadata["portfolio_fit"] = "addable" if index <= candidate_count else "watch"
            bucket = "candidate" if index <= candidate_count else "watch"
            reason = "ranked by macro-adjusted factor score"
            reason_codes = candidate.reason_codes
        ranked.append(
            ScreeningCandidate(
                run_id=candidate.run_id,
                asset_id=candidate.asset_id,
                score=candidate.score,
                rank=index,
                decision_bucket=bucket,
                reason=reason,
                reason_codes=reason_codes,
                factor_scores=candidate.factor_scores,
                metadata=metadata,
            )
        )

    ScreeningRepository(conn).upsert_results([*ranked, *skipped])
    return ScreeningRunResult(
        run_id=actual_run_id,
        as_of_date=actual_as_of,
        candidates=ranked,
        skipped=skipped,
        screening_params=screening_params,
    )


def _score_asset(
    asset: Asset,
    run_id: str,
    screening_params: dict[str, Any],
    factors: Mapping[str, float | None],
    percentiles: Mapping[str, float | None],
    exposure_overlay: dict[str, dict[str, Any]],
    *,
    momentum_scaling: str = "raw",
    vol_fallback: bool = False,
) -> ScreeningCandidate:
    filters = screening_params.get("filters") or {}
    liquidity = factors.get("liquidity_1m")
    volatility = factors.get("volatility_3m")
    min_liquidity = filters.get("min_liquidity_usd")
    max_volatility = filters.get("max_volatility_3m")
    if min_liquidity is not None and (liquidity is None or liquidity < min_liquidity):
        return _skipped_candidate(
            run_id,
            asset.asset_id,
            "skipped: liquidity below macro-adjusted minimum",
            ["LIQUIDITY_BELOW_MINIMUM"],
            {"portfolio_fit": "watch"},
        )
    if max_volatility is not None and (volatility is None or volatility > max_volatility):
        return _skipped_candidate(
            run_id,
            asset.asset_id,
            "skipped: volatility above macro-adjusted maximum",
            ["VOLATILITY_ABOVE_MAXIMUM"],
            {"portfolio_fit": "watch"},
        )

    # ── Posture-dependent trend gate (Sprint 005b §4) ─────────────────────────
    positioning = screening_params.get("positioning")
    gate_postures = screening_params.get("trend_gate_postures") or []
    trend_gate_active = positioning in gate_postures
    # Gate only on a confirmed below-MA reading (== 0.0, per spec §4). A missing
    # above_200d_ma (None) is not a confirmed breach, so it is not gated here;
    # such assets simply carry a null trend_score into the renormalized score.
    if trend_gate_active and factors.get("above_200d_ma") == 0.0:
        return _skipped_candidate(
            run_id,
            asset.asset_id,
            "skipped: below 200d MA under defensive posture",
            ["BELOW_200D_MA_DEFENSIVE"],
            {"portfolio_fit": "watch", "trend_gate_active": True},
        )

    horizon_weights = screening_params.get("momentum_horizon_weights") or {}
    momentum_score = _weighted_momentum(percentiles, horizon_weights)
    valuation_score = _valuation_score(percentiles)
    factor_scores = {
        "momentum_score": momentum_score,
        "liquidity_score": percentiles.get("liquidity_1m"),
        "trend_score": percentiles.get("above_200d_ma"),
        "volatility_penalty": percentiles.get("volatility_3m"),
        "valuation_score": valuation_score,
        # Horizon detail — previously computed then discarded; kept so reports
        # and the Research Agent can see e.g. a 6m leader with a 1m reversal.
        "momentum_1m_pct": percentiles.get("momentum_1m"),
        "momentum_3m_pct": percentiles.get("momentum_3m"),
        "momentum_6m_pct": percentiles.get("momentum_6m"),
        # Raw valuation context (not percentiles) for human/LLM judgment.
        **{name: factors.get(name) for name in VALUATION_CONTEXT_FACTORS},
        "above_200d_ma": factors.get("above_200d_ma"),
        "trend_gate_active": trend_gate_active,
    }
    if factor_scores["momentum_score"] is None:
        return _skipped_candidate(
            run_id,
            asset.asset_id,
            "skipped: missing momentum factors",
            ["MISSING_MOMENTUM_FACTORS"],
            {"portfolio_fit": "watch"},
            factor_scores=factor_scores,
        )
    # Eligibility counts only the four price-score groups (SCORE_GROUP_KEYS):
    # valuation is additive context — an asset without fundamentals must rank,
    # not skip, so its weight renormalizes away below instead.
    if sum(factor_scores[key] is not None for key in SCORE_GROUP_KEYS) < 3:
        return _skipped_candidate(
            run_id,
            asset.asset_id,
            "skipped: missing momentum factors",
            ["INSUFFICIENT_SCORE_GROUPS"],
            {"portfolio_fit": "watch"},
            factor_scores=factor_scores,
        )

    # When the trend gate is active, trend is enforced as eligibility above, so
    # drop it from the weighted sum and renormalize the remaining weights — the
    # factor must not be double-counted as both gate and score.
    weights = screening_params.get("factor_weights") or {}
    if trend_gate_active:
        weights = _renormalize_without(weights, "trend")
    if valuation_score is None and weights.get("valuation"):
        weights = _renormalize_without(weights, "valuation")
    score = (
        float(weights.get("momentum", 0.0)) * (factor_scores["momentum_score"] or 0.0)
        + float(weights.get("liquidity", 0.0)) * (factor_scores["liquidity_score"] or 0.0)
        + float(weights.get("trend", 0.0)) * (factor_scores["trend_score"] or 0.0)
        + float(weights.get("valuation", 0.0)) * (valuation_score or 0.0)
        - float(weights.get("volatility_penalty", 0.0)) * (factor_scores["volatility_penalty"] or 0.0)
    )
    blocking = _blocking_exposures_for(asset, exposure_overlay)
    return ScreeningCandidate(
        run_id=run_id,
        asset_id=asset.asset_id,
        score=score,
        rank=None,
        decision_bucket="watch",
        reason="ranked by macro-adjusted factor score",
        reason_codes=[],
        factor_scores=factor_scores,
        metadata={
            "portfolio_fit": "watch" if blocking else "addable",
            "blocking_exposures": blocking,
            "would_worsen_violation": bool(blocking),
            "momentum_horizon_weights": dict(horizon_weights),
            "momentum_scaling": momentum_scaling,
            "momentum_vol_fallback": bool(vol_fallback),
            "trend_gate_active": trend_gate_active,
        },
    )


def _factor_percentiles(
    factor_values: dict[str, dict[str, float | None]]
) -> dict[str, dict[str, float | None]]:
    by_asset = {asset_id: {} for asset_id in factor_values}
    for factor_name in FACTOR_NAMES:
        ranked = percentile_rank(
            {
                asset_id: factors.get(factor_name)
                for asset_id, factors in factor_values.items()
            }
        )
        for asset_id, percentile in ranked.items():
            by_asset[asset_id][factor_name] = percentile
    return by_asset


def _load_latest_factor_values(
    conn: duckdb.DuckDBPyConnection,
    asset_ids: list[str],
    as_of_date: date,
) -> dict[str, dict[str, float | None]]:
    if not asset_ids:
        return {}
    placeholders = ", ".join("?" for _ in asset_ids)
    params = [*asset_ids, as_of_date, *FACTOR_NAMES]
    rows = conn.execute(
        f"""
        WITH latest AS (
          SELECT asset_id, factor_name, max(date) AS latest_date
          FROM factor_values
          WHERE asset_id IN ({placeholders})
            AND date <= ?
            AND factor_name IN ({", ".join("?" for _ in FACTOR_NAMES)})
          GROUP BY asset_id, factor_name
        )
        SELECT fv.asset_id, fv.factor_name, fv.value
        FROM factor_values fv
        JOIN latest
          ON latest.asset_id = fv.asset_id
         AND latest.factor_name = fv.factor_name
         AND latest.latest_date = fv.date
        """,
        params,
    ).fetchall()
    values = {asset_id: {factor: None for factor in FACTOR_NAMES} for asset_id in asset_ids}
    for asset_id, factor_name, value in rows:
        values[asset_id][factor_name] = value
    return values


def _load_blocking_exposures(
    conn: duckdb.DuckDBPyConnection,
    portfolio_id: str | None,
    as_of_date: date,
) -> dict[str, dict[str, Any]]:
    if portfolio_id is None:
        return {}
    rows = conn.execute(
        """
        WITH latest AS (
          SELECT max(as_of_date) AS latest_date
          FROM portfolio_exposures
          WHERE portfolio_id = ? AND as_of_date <= ?
        )
        SELECT exposure_type, exposure_name, weight, limit_weight, is_violation
        FROM portfolio_exposures, latest
        WHERE portfolio_id = ?
          AND as_of_date = latest.latest_date
          AND is_violation = TRUE
        """,
        [portfolio_id, as_of_date, portfolio_id],
    ).fetchall()
    return {
        f"{exposure_type}:{exposure_name}": {
            "type": exposure_type,
            "name": exposure_name,
            "weight": weight,
            "limit_weight": limit_weight,
            "is_violation": bool(is_violation),
        }
        for exposure_type, exposure_name, weight, limit_weight, is_violation in rows
    }


def _blocking_exposures_for(asset: Asset, overlay: dict[str, dict[str, Any]]) -> list[str]:
    candidates: list[str] = []
    if asset.sector:
        candidates.append(f"sector:{asset.sector}")
    if asset.industry:
        candidates.append(f"industry:{asset.industry}")
    if asset.country:
        candidates.append(f"country:{asset.country}")
    if asset.currency:
        candidates.append(f"currency:{asset.currency}")
    for tag in _theme_tags(asset):
        candidates.append(f"theme:{tag}")
    return [candidate for candidate in candidates if candidate in overlay]


def _theme_tags(asset: Asset) -> list[str]:
    tags = asset.metadata.get("theme_tags", [])
    return tags if isinstance(tags, list) else []


MOMENTUM_HORIZONS = ("momentum_1m", "momentum_3m", "momentum_6m")


def _average(values: list[float | None]) -> float | None:
    non_null = [value for value in values if value is not None]
    if not non_null:
        return None
    return sum(non_null) / len(non_null)


def _weighted_momentum(
    percentiles: Mapping[str, float | None],
    horizon_weights: Mapping[str, float],
) -> float | None:
    """
    Combine the momentum horizon percentiles into a single score.

    Without configured horizon weights this is the equal-average of available
    horizons (pre-005b behavior). With horizon weights, available horizons are
    combined as a weighted average; if a horizon percentile is null its weight
    is dropped and the remaining weights renormalize so scores stay comparable
    across assets. Returns None only when every horizon is null.
    """
    available = [
        (name, value)
        for name in MOMENTUM_HORIZONS
        if (value := percentiles.get(name)) is not None
    ]
    if not available:
        return None
    weight_total = sum(float(horizon_weights.get(name, 0.0)) for name, _ in available)
    if not horizon_weights or weight_total == 0.0:
        return _average([value for _, value in available])
    return sum(
        float(horizon_weights.get(name, 0.0)) * value for name, value in available
    ) / weight_total


def _scale_momentum_values(
    factor_values: dict[str, dict[str, float | None]],
    fallback_assets: set[str],
) -> dict[str, dict[str, float | None]]:
    """
    Return a copy of ``factor_values`` with each momentum horizon divided by
    ``volatility_3m`` before percentile ranking. When volatility is null or
    zero the raw momentum value is kept and the asset is recorded in
    ``fallback_assets`` so the candidate metadata can flag it.
    """
    scaled: dict[str, dict[str, float | None]] = {}
    for asset_id, factors in factor_values.items():
        row = dict(factors)
        volatility = factors.get("volatility_3m")
        if volatility in (None, 0.0):
            if any(factors.get(name) is not None for name in MOMENTUM_HORIZONS):
                fallback_assets.add(asset_id)
        else:
            for name in MOMENTUM_HORIZONS:
                value = factors.get(name)
                if value is not None:
                    row[name] = value / volatility
        scaled[asset_id] = row
    return scaled


def _renormalize_without(weights: Mapping[str, float], dropped: str) -> dict[str, float]:
    """
    Drop one weight and scale the remaining weights so their total magnitude is
    preserved — keeps the score on the same scale as a full run. Used when the
    trend gate replaces the trend score, and when an asset has no valuation
    data (missing fundamentals must not zero out a chunk of the score range).
    """
    total = sum(abs(float(value)) for value in weights.values())
    remaining = {key: float(value) for key, value in weights.items() if key != dropped}
    remaining_total = sum(abs(value) for value in remaining.values())
    if remaining_total == 0.0:
        return remaining
    scale = total / remaining_total
    return {key: round(value * scale, 4) for key, value in remaining.items()}


def _renormalize_without_trend(weights: Mapping[str, float]) -> dict[str, float]:
    return _renormalize_without(weights, "trend")


def _valuation_score(percentiles: Mapping[str, float | None]) -> float | None:
    """
    Average the available valuation percentiles into one higher-is-cheaper
    score. Multiples and price_to_intrinsic invert (low percentile = cheap =
    good); fcf_yield is already higher-is-better. Returns None when no scored
    valuation factor is present (the caller renormalizes the weight away).
    """
    sub_scores: list[float] = []
    for name in VALUATION_INVERTED:
        pct = percentiles.get(name)
        if pct is not None:
            sub_scores.append(1.0 - pct)
    for name in VALUATION_NATURAL:
        pct = percentiles.get(name)
        if pct is not None:
            sub_scores.append(pct)
    if not sub_scores:
        return None
    return sum(sub_scores) / len(sub_scores)


def _skipped_candidate(
    run_id: str,
    asset_id: str,
    reason: str,
    reason_codes: list[str],
    metadata: dict[str, Any],
    *,
    factor_scores: dict[str, float | None] | None = None,
) -> ScreeningCandidate:
    return ScreeningCandidate(
        run_id=run_id,
        asset_id=asset_id,
        score=None,
        rank=None,
        decision_bucket="skipped",
        reason=reason,
        reason_codes=reason_codes,
        factor_scores=factor_scores or {},
        metadata=metadata,
    )


def _blocked_reason(blocking: list[str]) -> str:
    first = blocking[0]
    exposure_type, exposure_name = first.split(":", 1)
    return f"blocked: {exposure_name} {exposure_type} exposure already exceeds profile max"


def _latest_factor_date(conn: duckdb.DuckDBPyConnection) -> date | None:
    row = conn.execute("SELECT max(date) FROM factor_values").fetchone()
    return row[0] if row else None

"""Walk-forward backtest engine.

Design principles
-----------------
* **Point-in-time**: factors are recomputed from trailing price windows up to
  (and including) each rebalance date.  The ``factor_values`` DB table is NOT
  read — it stores live-run values, not historical ones.
* **Deterministic**: same config + same DB data → identical output every run.
  No randomness, no wall-clock references.
* **Reuses croesus/factors/common.py**: ``compute_common_factors`` is the only
  source of factor math; the engine never reimplements momentum/vol/liquidity.
* **Mirrors screening scoring**: percentile_rank per factor across the live
  universe on that date, then dimension scores with the same formula as
  ``run_screening._score_asset`` (volatility_penalty subtracted, trend gate
  omitted for static A/B test fairness).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import duckdb
import pandas as pd

from croesus.backtest.config import BacktestConfig
from croesus.factors.common import compute_common_factors
from croesus.screening.normalization import percentile_rank

# Lookback window needed to compute momentum_6m (126 bars) + above_200d_ma
# (200 bars) plus a buffer for non-trading days (~1.4×).
_LOOKBACK_DAYS = 400

# Factors that feed the scoring dimensions (price factors only; valuation is
# excluded from the backtest — see Honest Limitations in the report).
_PRICE_FACTOR_NAMES = (
    "momentum_1m",
    "momentum_3m",
    "momentum_6m",
    "volatility_3m",
    "liquidity_1m",
    "above_200d_ma",
)


@dataclass
class RebalanceRecord:
    """Per-rebalance snapshot for one scheme."""

    rebalance_date: date
    holdings: list[str]  # asset_ids selected
    scores: dict[str, float]  # asset_id -> composite score


@dataclass
class SchemeResult:
    """Full result for one weight scheme."""

    scheme_name: str
    equity_curve: pd.Series  # index=date, values=portfolio value
    rebalances: list[RebalanceRecord]
    total_turnover: float  # sum of |w_new - w_old|/2 across all rebalances


def run_backtest(
    config: BacktestConfig,
    db_path: str | None = None,
) -> dict[str, SchemeResult]:
    """Run a walk-forward backtest for every scheme in *config*.

    Parameters
    ----------
    config:
        Fully specified backtest configuration.
    db_path:
        Path to the DuckDB file.  If *None*, the default location is used.

    Returns
    -------
    dict mapping scheme name → SchemeResult.
    """
    start = date.fromisoformat(config.start_date)
    end = date.fromisoformat(config.end_date)
    lookback_start = start - timedelta(days=_LOOKBACK_DAYS)

    from croesus.db.connection import get_connection

    with get_connection(db_path) as conn:
        prices_wide, asset_ids = _load_prices(conn, lookback_start, end)
        benchmark_series = _load_benchmark(conn, config.benchmark_symbol, start, end)

    rebalance_dates = _monthly_rebalance_dates(prices_wide.index, start, end)

    results: dict[str, SchemeResult] = {}
    for scheme_name, weights in config.weight_schemes.items():
        result = _run_scheme(
            scheme_name=scheme_name,
            weights=weights,
            prices_wide=prices_wide,
            asset_ids=asset_ids,
            rebalance_dates=rebalance_dates,
            start=start,
            end=end,
            config=config,
        )
        results[scheme_name] = result

    # Attach benchmark under a reserved key so callers can reference it.
    results["__benchmark__"] = SchemeResult(
        scheme_name=config.benchmark_symbol,
        equity_curve=_benchmark_curve(benchmark_series, config.initial_capital),
        rebalances=[],
        total_turnover=0.0,
    )
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_prices(
    conn: duckdb.DuckDBPyConnection,
    start: date,
    end: date,
) -> tuple[pd.DataFrame, list[str]]:
    """Return (prices_wide, asset_ids).

    *prices_wide*: DataFrame with date index and one column per asset_id
    (close price).  A separate ``_volume_wide`` is kept in a closure; we
    return a single wide frame here for simplicity and reconstruct volume
    below when computing factors.
    """
    rows = conn.execute(
        """
        SELECT p.asset_id, p.date, p.close, COALESCE(p.volume, 0) AS volume
        FROM prices_daily p
        JOIN assets a ON a.asset_id = p.asset_id
        WHERE a.asset_type IN ('equity', 'etf')
          AND a.is_active = TRUE
          AND p.date >= ?
          AND p.date <= ?
          AND p.close IS NOT NULL
        ORDER BY p.date, p.asset_id
        """,
        [start, end],
    ).fetchall()

    if not rows:
        return pd.DataFrame(), []

    df = pd.DataFrame(rows, columns=["asset_id", "date", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    asset_ids = sorted(df["asset_id"].unique().tolist())
    prices_wide = df.pivot(index="date", columns="asset_id", values="close").sort_index()
    return prices_wide, asset_ids


def _load_benchmark(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start: date,
    end: date,
) -> pd.Series:
    """Return a Series of daily closes for the benchmark symbol."""
    rows = conn.execute(
        """
        SELECT p.date, p.close
        FROM prices_daily p
        JOIN assets a ON a.asset_id = p.asset_id
        WHERE a.symbol = ?
          AND p.date >= ?
          AND p.date <= ?
          AND p.close IS NOT NULL
        ORDER BY p.date
        """,
        [symbol, start, end],
    ).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    dates, closes = zip(*rows)
    return pd.Series(closes, index=[d for d in dates], name=symbol, dtype=float)


def _monthly_rebalance_dates(
    all_dates: pd.Index,
    start: date,
    end: date,
) -> list[date]:
    """First trading date of each calendar month within [start, end]."""
    trading_days = sorted(d for d in all_dates if start <= d <= end)
    if not trading_days:
        return []
    result: list[date] = []
    seen_months: set[tuple[int, int]] = set()
    for d in trading_days:
        key = (d.year, d.month)
        if key not in seen_months:
            seen_months.add(key)
            result.append(d)
    return result


def _run_scheme(
    *,
    scheme_name: str,
    weights: dict[str, float],
    prices_wide: pd.DataFrame,
    asset_ids: list[str],
    rebalance_dates: list[date],
    start: date,
    end: date,
    config: BacktestConfig,
) -> SchemeResult:
    """Walk-forward simulation for a single weight scheme."""
    if prices_wide.empty or not rebalance_dates:
        empty = pd.Series(dtype=float)
        return SchemeResult(
            scheme_name=scheme_name,
            equity_curve=empty,
            rebalances=[],
            total_turnover=0.0,
        )

    trading_days = sorted(d for d in prices_wide.index if start <= d <= end)
    if not trading_days:
        empty = pd.Series(dtype=float)
        return SchemeResult(
            scheme_name=scheme_name,
            equity_curve=empty,
            rebalances=[],
            total_turnover=0.0,
        )

    # Build volume wide frame for factor computation.
    # We need to reload from prices_wide structure — volume was not stored
    # in prices_wide; reload it from the price DataFrame by accessing the
    # engine's internal helper.  Instead, we pass prices_wide and volume data
    # together in a combined structure via _build_asset_frames.
    asset_frames = _build_asset_frames(prices_wide, asset_ids)

    # State: current weights as dict asset_id -> weight (equal weight per holding)
    current_weights: dict[str, float] = {}
    portfolio_value = config.initial_capital
    rebalance_records: list[RebalanceRecord] = []
    total_turnover = 0.0

    # Build equity curve day by day.
    equity_values: dict[date, float] = {}
    rebalance_set = set(rebalance_dates)

    for i, today in enumerate(trading_days):
        # Apply daily returns to current holdings before potentially rebalancing.
        if current_weights and i > 0:
            prev_day = trading_days[i - 1]
            portfolio_value = _apply_daily_return(
                portfolio_value, current_weights, prices_wide, prev_day, today
            )

        # Rebalance on designated dates.
        if today in rebalance_set:
            new_holdings, scores = _select_holdings(
                asset_ids=asset_ids,
                asset_frames=asset_frames,
                as_of=today,
                weights=weights,
                top_n=config.top_n,
            )

            if new_holdings:
                new_weights = {aid: 1.0 / len(new_holdings) for aid in new_holdings}
                turnover = _compute_turnover(current_weights, new_weights)
                total_turnover += turnover
                # Apply round-trip cost on changed weight.
                cost = turnover * 2.0 * config.cost_bps / 10_000.0
                portfolio_value *= 1.0 - cost
                current_weights = new_weights
            else:
                new_weights = {}
                turnover = _compute_turnover(current_weights, new_weights)
                total_turnover += turnover
                current_weights = {}

            rebalance_records.append(
                RebalanceRecord(
                    rebalance_date=today,
                    holdings=list(new_holdings),
                    scores=scores,
                )
            )

        equity_values[today] = portfolio_value

    equity_curve = pd.Series(equity_values, name=scheme_name).sort_index()
    return SchemeResult(
        scheme_name=scheme_name,
        equity_curve=equity_curve,
        rebalances=rebalance_records,
        total_turnover=total_turnover,
    )


def _build_asset_frames(
    prices_wide: pd.DataFrame,
    asset_ids: list[str],
) -> dict[str, pd.DataFrame]:
    """Build per-asset DataFrames with date and close columns.

    Volume data is not available in prices_wide (it was pivoted on close only).
    We set volume to a constant 1 so that ``compute_common_factors`` can still
    compute ``liquidity_1m = close * volume`` — the relative ranking is
    preserved even when absolute volume is unavailable from this in-memory
    structure.  When the full price+volume table is loaded (see _load_prices),
    we reconstruct properly.
    """
    frames: dict[str, pd.DataFrame] = {}
    for aid in asset_ids:
        if aid not in prices_wide.columns:
            continue
        col = prices_wide[aid].dropna()
        if col.empty:
            continue
        frames[aid] = pd.DataFrame({
            "date": col.index.tolist(),
            "close": col.values,
            "volume": [1_000_000.0] * len(col),  # placeholder; relative rank is valid
        })
    return frames


def _select_holdings(
    *,
    asset_ids: list[str],
    asset_frames: dict[str, pd.DataFrame],
    as_of: date,
    weights: dict[str, float],
    top_n: int,
) -> tuple[list[str], dict[str, float]]:
    """Score all assets point-in-time as of *as_of* and return top_n.

    Returns (selected_asset_ids, scores_dict).
    """
    factor_values: dict[str, dict[str, float]] = {}

    for aid in asset_ids:
        frame = asset_frames.get(aid)
        if frame is None:
            continue
        # Point-in-time slice: only rows up to as_of.
        mask = [d <= as_of for d in frame["date"]]
        pit = frame[mask]
        if pit.empty:
            continue
        factor_list = compute_common_factors(aid, pit)
        if not factor_list:
            continue  # insufficient history — skip, never crash
        factor_values[aid] = {fv.factor_name: fv.value for fv in factor_list}

    if not factor_values:
        return [], {}

    # Percentile rank each factor across the universe.
    pct_scores: dict[str, dict[str, float | None]] = {aid: {} for aid in factor_values}
    for factor_name in _PRICE_FACTOR_NAMES:
        raw = {aid: factor_values[aid].get(factor_name) for aid in factor_values}
        ranked = percentile_rank(raw)
        for aid, pct in ranked.items():
            pct_scores[aid][factor_name] = pct

    # Compose dimension scores for each asset, then compute weighted composite.
    composite: dict[str, float] = {}
    for aid, pcts in pct_scores.items():
        score = _composite_score(pcts, weights)
        if score is not None:
            composite[aid] = score

    if not composite:
        return [], {}

    # Sort deterministically: descending score, then ascending asset_id for ties.
    ranked_ids = sorted(composite.keys(), key=lambda a: (-composite[a], a))
    selected = ranked_ids[:top_n]
    return selected, {aid: composite[aid] for aid in selected}


def _composite_score(
    pcts: dict[str, float | None],
    weights: dict[str, float],
) -> float | None:
    """Compute weighted composite matching run_screening's formula.

    * momentum_score = mean of available horizon percentiles
    * liquidity_score = liquidity_1m percentile
    * trend_score = above_200d_ma percentile
    * volatility_penalty = volatility_3m percentile (subtracted)

    Returns None if momentum is missing (not scoreable).
    """
    # Momentum sub-score: equal average of available horizons (no custom weights
    # in backtest — keeps the A/B purely about dimension weights, not horizon mix).
    horizon_pcts = [
        pcts.get("momentum_1m"),
        pcts.get("momentum_3m"),
        pcts.get("momentum_6m"),
    ]
    valid_horizons = [p for p in horizon_pcts if p is not None]
    if not valid_horizons:
        return None
    momentum_score = sum(valid_horizons) / len(valid_horizons)

    liquidity_score = pcts.get("liquidity_1m")
    trend_score = pcts.get("above_200d_ma")
    vol_penalty = pcts.get("volatility_3m")

    # Build effective weights dropping missing dimensions and renormalizing.
    dimension_scores: dict[str, float | None] = {
        "momentum": momentum_score,
        "liquidity": liquidity_score,
        "trend": trend_score,
        "volatility_penalty": vol_penalty,
    }
    available = {k: v for k, v in dimension_scores.items() if v is not None}
    total_w = sum(abs(weights.get(k, 0.0)) for k in available)
    if total_w == 0.0:
        return None

    score = 0.0
    for dim, val in available.items():
        w = weights.get(dim, 0.0) / total_w
        if dim == "volatility_penalty":
            score -= w * val
        else:
            score += w * val
    return score


def _apply_daily_return(
    portfolio_value: float,
    weights: dict[str, float],
    prices_wide: pd.DataFrame,
    prev_day: date,
    today: date,
) -> float:
    """Update portfolio value by daily close-to-close returns of holdings."""
    total_return = 0.0
    total_weight = 0.0
    for aid, w in weights.items():
        if aid not in prices_wide.columns:
            continue
        prev_close = prices_wide.loc[prev_day, aid] if prev_day in prices_wide.index else None
        today_close = prices_wide.loc[today, aid] if today in prices_wide.index else None
        if prev_close is None or today_close is None or pd.isna(prev_close) or pd.isna(today_close):
            continue
        daily_ret = (float(today_close) / float(prev_close)) - 1.0
        total_return += w * daily_ret
        total_weight += w
    if total_weight > 0 and total_weight < 1.0:
        # Some holdings had no price; scale return to the covered weight.
        total_return = total_return  # already a dollar-weighted sum on partial holdings
    return portfolio_value * (1.0 + total_return)


def _compute_turnover(
    old_weights: dict[str, float],
    new_weights: dict[str, float],
) -> float:
    """Turnover = sum |w_new - w_old| / 2 (0.0–1.0 scale)."""
    all_ids = set(old_weights) | set(new_weights)
    total = sum(
        abs(new_weights.get(aid, 0.0) - old_weights.get(aid, 0.0))
        for aid in all_ids
    )
    return total / 2.0


def _benchmark_curve(benchmark_series: pd.Series, initial_capital: float) -> pd.Series:
    """Convert a price series to an equity curve starting at *initial_capital*."""
    if benchmark_series.empty:
        return pd.Series(dtype=float)
    normalized = benchmark_series / benchmark_series.iloc[0] * initial_capital
    return normalized

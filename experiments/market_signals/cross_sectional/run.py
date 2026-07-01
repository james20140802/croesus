"""로드맵 ① — Cross-sectional Information Coefficient harness.

Run from repo root:  python -m experiments.market_signals.cross_sectional.run

Builds the point-in-time factor panel over the full price history, then writes:
  results/cross_sectional/panel.csv            — the raw long panel
  results/cross_sectional/ic_summary.csv        — factor x horizon IC stats
  results/cross_sectional/longshort_summary.csv — Q5-Q1 non-overlapping performance
  results/cross_sectional/perdate_<f>_<h>.csv   — per-rebalance IC / LS / n
  results/cross_sectional/permutation.csv       — observed vs shuffled-null mean IC

IC is estimated on every monthly cross-section (max data); its Newey-West t-stat
uses a HAC lag equal to the forward-return overlap (h / 21) so overlapping windows
do not inflate significance. The tradeable long-short equity curve is instead built
on NON-OVERLAPPING holding periods (rebalance every h days) so returns compound
correctly — compounding overlapping h-day returns monthly is invalid and grossly
overstates cumulative performance.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.cross_sectional.factors import FACTOR_NAMES
from experiments.market_signals.cross_sectional.ic import spearman_ic, summarize_ic
from experiments.market_signals.cross_sectional.panel import (
    build_panel,
    equal_weight_market_return,
    month_end_grid,
)
from experiments.market_signals.cross_sectional.portfolio import (
    long_short_return,
    perf_summary,
    quintile_buckets,
)
from experiments.market_signals.cross_sectional.stats import permutation_ic_null
from experiments.market_signals.cross_sectional.universe import load_universe_prices

HORIZONS = [21, 63, 126]
COST_BPS = [0.0, 10.0, 20.0]  # per-leg per-rebalance turnover cost sensitivity
TRADING_DAYS = 252

# Long-history mode (CS_LONG=1) reads the yfinance scratch DB (1990+) instead of
# the production DB (~2016+), and writes results/output to a separate subdir so
# the two runs don't overwrite each other.
LONG = bool(os.environ.get("CS_LONG"))
START_YEAR = int(os.environ.get("CS_START_YEAR", "1995" if LONG else "2010"))
OUT = Path(RESULTS_DIR) / ("cross_sectional_long" if LONG else "cross_sectional")


def _top_bottom_sets(g: pd.DataFrame, q: int = 5):
    df = g[["asset_id", "value"]].dropna()
    if len(df) < q:
        return set(), set()
    b = quintile_buckets(df["value"], q)
    top = set(df.loc[b[b == q].index, "asset_id"])
    bot = set(df.loc[b[b == 1].index, "asset_id"])
    return top, bot


def _per_date_ic(sub: pd.DataFrame, col: str) -> pd.DataFrame:
    """Per-rebalance IC and long-short return over every monthly cross-section."""
    rows = []
    for dt, g in sub[sub[col].notna()].groupby("date"):
        rows.append({
            "date": dt,
            "ic": spearman_ic(g["value"], g[col]),
            "ls": long_short_return(g["value"], g[col], 5),
            "n": int(g["value"].notna().sum()),
        })
    return pd.DataFrame(rows)


def _nonoverlap_backtest(sub: pd.DataFrame, col: str, h: int) -> tuple[pd.DataFrame, float]:
    """Long-short equity curve on non-overlapping h-day holding periods.

    Rebalances every ``step = round(h/21)`` months so consecutive h-day forward
    returns do not overlap. Returns (per-period frame with ls + turnover, ppy).
    """
    step = max(1, int(round(h / 21)))
    ppy = TRADING_DAYS / h
    dates = sorted(sub.loc[sub[col].notna(), "date"].unique())
    entry_dates = dates[::step]
    prev_top: set = set()
    prev_bot: set = set()
    rows = []
    for dt in entry_dates:
        g = sub[(sub["date"] == dt) & sub[col].notna()]
        ls = long_short_return(g["value"], g[col], 5)
        top, bot = _top_bottom_sets(g)
        if prev_top or prev_bot:
            t_top = len(top - prev_top) / len(top) if top else 0.0
            t_bot = len(bot - prev_bot) / len(bot) if bot else 0.0
            to = 0.5 * (t_top + t_bot)
        else:
            to = np.nan
        rows.append({"date": dt, "ls": ls, "turnover": to})
        prev_top, prev_bot = top, bot
    return pd.DataFrame(rows), ppy


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / "panel.csv"
    if cache.exists() and not os.environ.get("CS_REBUILD"):
        print(f"[cross_sectional] reusing cached panel {cache} (set CS_REBUILD=1 to rebuild)")
        panel = pd.read_csv(cache, parse_dates=["date"])
    else:
        if LONG:
            from experiments.market_signals.cross_sectional.history import load_long_history
            print(f"[cross_sectional] LONG mode: loading yfinance history (>= {START_YEAR}) ...")
            prices = load_long_history(start_year=START_YEAR)
        else:
            print("[cross_sectional] loading universe ...")
            prices = load_universe_prices()
        print(f"[cross_sectional] {len(prices)} assets")
        all_dates = sorted({d for g in prices.values() for d in g.index})
        grid = month_end_grid(all_dates, START_YEAR)
        print(f"[cross_sectional] {len(grid)} monthly rebalances "
              f"({pd.Timestamp(grid[0]).date()}..{pd.Timestamp(grid[-1]).date()})")
        market = equal_weight_market_return(prices)
        print("[cross_sectional] building panel ...")
        panel = build_panel(prices, grid, HORIZONS, market)
        panel.to_csv(cache, index=False)
    print(f"[cross_sectional] panel rows: {len(panel):,}")

    ic_rows, ls_rows = [], []
    for fac in FACTOR_NAMES:
        sub = panel[panel["factor_name"] == fac]
        if sub.empty:
            continue
        for h in HORIZONS:
            col = f"fwd_{h}"
            overlap = max(1, int(round(h / 21)))

            per_date = _per_date_ic(sub, col)
            if per_date.empty:
                continue
            per_date.to_csv(OUT / f"perdate_{fac}_{h}.csv", index=False)

            summ = summarize_ic(per_date["ic"], lags=(None if overlap <= 1 else overlap))
            summ.update({"factor": fac, "h": h, "avg_n": float(per_date["n"].mean())})
            ic_rows.append(summ)

            bt, ppy = _nonoverlap_backtest(sub, col, h)
            gross = bt["ls"].dropna()
            avg_turnover = float(bt["turnover"].dropna().mean()) if bt["turnover"].notna().any() else 0.0
            for bps in COST_BPS:
                net = bt["ls"] - (bt["turnover"].fillna(0.0) * 2 * bps / 1e4)
                perf = perf_summary(net.dropna(), ppy)
                perf.update({"factor": fac, "h": h, "cost_bps": bps,
                             "n_periods": int(gross.shape[0]), "avg_turnover": avg_turnover})
                ls_rows.append(perf)

    ic_df = pd.DataFrame(ic_rows)
    ic_df.to_csv(OUT / "ic_summary.csv", index=False)
    pd.DataFrame(ls_rows).to_csv(OUT / "longshort_summary.csv", index=False)

    # Permutation null for mean IC at the representative horizon (63d).
    perm_rows = []
    for fac in FACTOR_NAMES:
        sub = panel[(panel["factor_name"] == fac) & panel["fwd_63"].notna()]
        if sub.empty:
            continue
        obs = pd.to_numeric(
            sub.groupby("date").apply(lambda g: spearman_ic(g["value"], g["fwd_63"])),
            errors="coerce",
        ).dropna().mean()
        null_means = []
        for _, g in list(sub.groupby("date")):
            nm = permutation_ic_null(g["value"].to_numpy(), g["fwd_63"].to_numpy(), n=100, seed=1).mean()
            if np.isfinite(nm):
                null_means.append(nm)
        null_means = np.asarray(null_means)
        perm_rows.append({
            "factor": fac, "obs_mean_ic": float(obs),
            "null_mean": float(null_means.mean()) if null_means.size else float("nan"),
            "null_sd": float(null_means.std()) if null_means.size else float("nan"),
        })
    pd.DataFrame(perm_rows).to_csv(OUT / "permutation.csv", index=False)

    print("\n[cross_sectional] IC summary (factor x horizon):")
    if not ic_df.empty:
        print(ic_df[["factor", "h", "mean", "t_nw", "ir", "hit_rate", "n"]].to_string(index=False))
    print(f"\n[cross_sectional] wrote results to {OUT}")


if __name__ == "__main__":
    main()

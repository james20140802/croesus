"""Survivorship-bias sensitivity simulation (로드맵 ① 후속).

Run from repo root (needs the 30-year panel from CS_LONG run):
  CS_LONG=1 python -m experiments.market_signals.cross_sectional.run          # builds panel
  python -m experiments.market_signals.cross_sectional.run_survivorship        # this sim

Idea: we can't add delisted stocks (yfinance has none), so we bound the bias.
Each month we delist a fraction of *fragile* names (high volatility / low liquidity)
for cause, hand them a large terminal loss, and drop them thereafter. Because the
high-vol / high-beta long leg holds the fragile names, this tests whether the
"high-risk wins" premium survives realistic delisting. We sweep the assumed annual
for-cause delisting rate and report each signal's mean IC and Q5-Q1 Sharpe.

Rebalance is monthly with 21-day forward returns (non-overlapping).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.cross_sectional.ic import spearman_ic
from experiments.market_signals.cross_sectional.portfolio import long_short_return, perf_summary
from experiments.market_signals.cross_sectional.survivorship import (
    draw_terminal_returns,
    fragility_percentile,
    hazard_prob,
)

LONG_DIR = Path(RESULTS_DIR) / "cross_sectional_long"
OUT = LONG_DIR
SIGNALS = ["volatility_3m", "beta_1y", "liquidity_1m", "momentum_6m"]
ANNUAL_RATES = [0.0, 0.02, 0.04, 0.06, 0.08]  # assumed for-cause delisting rate/yr
# Assumptions are env-overridable so we can bracket the bias with a harsh scenario:
#   base:  CS_SURV_K=3  terminal ~U(-1.0,-0.5)   (avg -75% loss, moderate tilt)
#   harsh: CS_SURV_K=6  terminal =-1.0 (all -100%, strong tilt onto fragile names)
K_TILT = float(os.environ.get("CS_SURV_K", "3.0"))
TERM_LO = float(os.environ.get("CS_SURV_TLO", "-1.0"))
TERM_HI = float(os.environ.get("CS_SURV_THI", "-0.5"))
TAG = os.environ.get("CS_SURV_TAG", "")
N_SEEDS = 25
FWD = "fwd_21"
PPY = 12.0


def _wide_panel() -> pd.DataFrame:
    """Wide frame indexed by (date, asset_id) with signal columns + fwd_21."""
    panel = pd.read_csv(LONG_DIR / "panel.csv", parse_dates=["date"])
    need = set(SIGNALS) | {"volatility_3m", "liquidity_1m"}
    sub = panel[panel["factor_name"].isin(need)]
    wide = sub.pivot_table(index=["date", "asset_id"], columns="factor_name",
                           values="value", aggfunc="first")
    fwd = (panel[[FWD, "date", "asset_id"]].dropna(subset=[FWD])
           .drop_duplicates(["date", "asset_id"]).set_index(["date", "asset_id"])[FWD])
    wide[FWD] = fwd
    return wide.reset_index()


def _simulate(wide: pd.DataFrame, annual_rate: float, seed: int) -> dict:
    """One Monte-Carlo path: monthly delisting overlay, return per-signal metrics."""
    rng = np.random.default_rng(seed)
    base_monthly = annual_rate / 12.0
    dates = sorted(wide["date"].unique())
    alive = set(wide["asset_id"].unique())

    ic_acc = {s: [] for s in SIGNALS}
    ls_acc = {s: [] for s in SIGNALS}

    for dt in dates:
        g = wide[(wide["date"] == dt) & wide["asset_id"].isin(alive)].set_index("asset_id")
        g = g[g[FWD].notna()]
        if len(g) < 25:
            continue
        fwd = g[FWD].copy()

        # delisting overlay (rate 0 => no-op, reproduces survivor-only result)
        if base_monthly > 0 and "volatility_3m" in g:
            frag = fragility_percentile(g.get("volatility_3m"), g.get("liquidity_1m"))
            frag = frag.reindex(g.index).fillna(0.5)
            p = hazard_prob(frag, base_monthly, K_TILT)
            delisted = g.index[rng.random(len(g)) < p.to_numpy()]
            if len(delisted):
                fwd.loc[delisted] = draw_terminal_returns(delisted, rng, TERM_LO, TERM_HI).values
                alive.difference_update(set(delisted))

        for sig in SIGNALS:
            if sig not in g:
                continue
            vals = g[sig].dropna()
            common = vals.index.intersection(fwd.dropna().index)
            if len(common) < 25:
                continue
            ic_acc[sig].append(spearman_ic(vals.loc[common], fwd.loc[common]))
            ls_acc[sig].append(long_short_return(vals.loc[common], fwd.loc[common], 5))

    out = {}
    for sig in SIGNALS:
        ic = pd.Series(ic_acc[sig], dtype=float).dropna()
        ls = pd.Series(ls_acc[sig], dtype=float).dropna()
        out[sig] = {"mean_ic": float(ic.mean()) if len(ic) else np.nan,
                    "ls_sharpe": perf_summary(ls, PPY)["sharpe"],
                    "ls_mean": float(ls.mean()) if len(ls) else np.nan}
    return out


def main() -> None:
    if not (LONG_DIR / "panel.csv").exists():
        raise SystemExit("Run `CS_LONG=1 python -m ...cross_sectional.run` first to build the 30y panel.")
    wide = _wide_panel()
    print(f"[survivorship] wide panel: {len(wide):,} rows, "
          f"{wide['asset_id'].nunique()} assets, {wide['date'].nunique()} months")

    rows = []
    for rate in ANNUAL_RATES:
        per_seed = [_simulate(wide, rate, seed) for seed in range(N_SEEDS)]
        for sig in SIGNALS:
            ics = np.array([s[sig]["mean_ic"] for s in per_seed], dtype=float)
            shs = np.array([s[sig]["ls_sharpe"] for s in per_seed], dtype=float)
            rows.append({
                "signal": sig, "annual_delist_rate": rate,
                "mean_ic": float(np.nanmean(ics)), "mean_ic_sd": float(np.nanstd(ics)),
                "ls_sharpe": float(np.nanmean(shs)), "ls_sharpe_sd": float(np.nanstd(shs)),
            })
        print(f"[survivorship] rate={rate:.0%} done")
    df = pd.DataFrame(rows)
    suffix = f"_{TAG}" if TAG else ""
    df.to_csv(OUT / f"survivorship_sensitivity{suffix}.csv", index=False)
    print(f"[survivorship] scenario: K_TILT={K_TILT} terminal=U({TERM_LO},{TERM_HI}) tag='{TAG or 'base'}'")

    print("\n[survivorship] mean IC (h=21) by assumed for-cause delisting rate:")
    print(df.pivot(index="signal", columns="annual_delist_rate", values="mean_ic").round(4).to_string())
    print("\n[survivorship] Q5-Q1 Sharpe by assumed for-cause delisting rate:")
    print(df.pivot(index="signal", columns="annual_delist_rate", values="ls_sharpe").round(3).to_string())
    print(f"\n[survivorship] wrote {OUT / f'survivorship_sensitivity{suffix}.csv'}")


if __name__ == "__main__":
    main()

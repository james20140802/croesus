"""로드맵 ② orchestration — walk-forward vol forecasts + vol-targeting overlay.

Run from repo root (venv with experiments/market_signals/requirements.txt):
  python -m experiments.market_signals.vol_targeting.run
Env:
  VT_ASSETS=spy,ew    subset of assets (default both)
  VT_START_YEAR=1995  first rebalance year (warmup uses earlier data)

Walk-forward: at each month-end t, forecast annualized vol for the next 21
trading days using returns[:t] only; compare with forward realized vol
(accuracy) and set next month's exposure = min(cap, 0.15/sigma_hat) (overlay).
GARCH refits monthly on the trailing 2000 obs. 'oracle' uses the forward
realized vol itself — a look-ahead upper bound, reported for diagnostics only.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.cross_sectional.panel import month_end_grid
from experiments.market_signals.cross_sectional.portfolio import perf_summary
from experiments.market_signals.vol_targeting.data import fetch_spy, load_ew_returns, load_spy
from experiments.market_signals.vol_targeting.evaluate import loss_diff_tstat, mse_loss, qlike_loss
from experiments.market_signals.vol_targeting.forecasters import (
    ewma_forecast,
    garch11_forecast,
    naive_forecast,
)
from experiments.market_signals.vol_targeting.overlay import overlay_returns, target_exposure
from experiments.market_signals.vol_targeting.realized import daily_returns, forward_realized_vol

OUT = RESULTS_DIR / "vol_targeting"
HORIZON = 21
SIGMA_TARGET = 0.15
CAPS = [1.0, 1.5]
COSTS_BPS = [0.0, 10.0]
GARCH_WINDOW = 2000
MIN_WARMUP = 750
FORECASTERS = ["naive", "ewma", "garch"]
START_YEAR = int(os.environ.get("VT_START_YEAR", "1995"))


def _load_assets() -> dict[str, pd.Series]:
    which = os.environ.get("VT_ASSETS", "spy,ew").split(",")
    out: dict[str, pd.Series] = {}
    if "spy" in which:
        fetch_spy()
        out["spy"] = daily_returns(load_spy())
    if "ew" in which:
        out["ew"] = load_ew_returns()
    return out


def _forecast_table(returns: pd.Series) -> pd.DataFrame:
    """Monthly walk-forward forecasts + forward realized vol, one row per month-end."""
    grid = [d for d in month_end_grid(list(returns.index), START_YEAR)
            if returns.index.searchsorted(d, side="right") >= MIN_WARMUP]
    rows = []
    for i, dt in enumerate(grid):
        hist = returns.loc[:dt]
        rows.append({
            "date": dt,
            "naive": naive_forecast(hist),
            "ewma": ewma_forecast(hist),
            "garch": garch11_forecast(hist.iloc[-GARCH_WINDOW:], HORIZON),
            "realized": forward_realized_vol(returns, dt, HORIZON),
        })
        if (i + 1) % 60 == 0:
            print(f"  ... {i + 1}/{len(grid)} months", flush=True)
    return pd.DataFrame(rows).set_index("date")


def _accuracy(tbl: pd.DataFrame) -> pd.DataFrame:
    ok = tbl.dropna(subset=["realized"])
    rows = []
    base_mse = mse_loss(ok["naive"], ok["realized"])
    base_ql = qlike_loss(ok["naive"], ok["realized"])
    for f in FORECASTERS:
        sub = ok.dropna(subset=[f])
        mse = mse_loss(sub[f], sub["realized"])
        ql = qlike_loss(sub[f], sub["realized"])
        dm_mse = loss_diff_tstat(mse_loss(ok[f], ok["realized"]), base_mse)
        dm_ql = loss_diff_tstat(qlike_loss(ok[f], ok["realized"]), base_ql)
        rows.append({"forecaster": f, "n": len(sub),
                     "mse": float(np.nanmean(mse)), "qlike": float(np.nanmean(ql)),
                     "dm_t_mse_vs_naive": dm_mse["t"], "dm_t_qlike_vs_naive": dm_ql["t"]})
    return pd.DataFrame(rows)


def _overlays(returns: pd.Series, tbl: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    years = (returns.index[-1] - returns.index[0]).days / 365.25
    ppy = len(returns) / years
    strategies = {"bnh": None, "oracle": "realized", **{f: f for f in FORECASTERS}}
    rows, curves = [], {}
    for cap in CAPS:
        for cost in COSTS_BPS:
            daily = {}
            for name, col in strategies.items():
                if name == "bnh":
                    expo = pd.Series(1.0, index=tbl.index)
                else:
                    expo = tbl[col].map(lambda s: target_exposure(s, SIGMA_TARGET, cap))
                ret = overlay_returns(returns, expo, cost)
                daily[name] = ret
                p = perf_summary(ret, ppy)
                to = float(expo.diff().abs().mean()) if name != "bnh" else 0.0
                rows.append({"strategy": name, "cap": cap, "cost_bps": cost,
                             "sharpe": p["sharpe"], "maxdd": p["maxdd"], "cum": p["cum"],
                             "ann_vol": p["vol"] * np.sqrt(ppy),
                             "avg_monthly_turnover": to,
                             "avg_exposure": float(expo.mean())})
            curves[(cap, cost)] = pd.DataFrame(daily)
    return pd.DataFrame(rows), curves


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for asset, returns in _load_assets().items():
        print(f"[vt] {asset}: {len(returns)} days "
              f"{returns.index[0].date()}..{returns.index[-1].date()}", flush=True)
        tbl = _forecast_table(returns)
        tbl.to_csv(OUT / f"perdate_{asset}.csv")
        acc = _accuracy(tbl)
        acc.to_csv(OUT / f"accuracy_{asset}.csv", index=False)
        print(f"[vt] {asset} accuracy:\n{acc.round(4).to_string(index=False)}", flush=True)
        ov, curves = _overlays(returns, tbl)
        ov.to_csv(OUT / f"overlay_{asset}.csv", index=False)
        for (cap, cost), df in curves.items():
            df.to_csv(OUT / f"curve_{asset}_cap{cap:g}_c{cost:g}.csv")
        print(f"[vt] {asset} overlay:\n{ov.round(3).to_string(index=False)}", flush=True)
    print(f"[vt] wrote results to {OUT}", flush=True)


if __name__ == "__main__":
    main()

"""로드맵 ③ orchestration — 이벤트 소급 탐지 + CAAR + placebo + 포트폴리오.

Run from repo root:
  python3 -m experiments.market_signals.event_drift.run
Env:
  ED_MAX_ASSETS=25    자산 수 제한(스모크용; 0=전체)
  ED_START_YEAR=1990  이력 시작 연도
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.cross_sectional.history import load_long_history
from experiments.market_signals.cross_sectional.portfolio import perf_summary
from experiments.market_signals.event_drift.caar import caar_table, placebo_events
from experiments.market_signals.event_drift.car import asset_excess, event_car_paths
from experiments.market_signals.event_drift.detect import dedupe_events, scan_asset_events
from experiments.market_signals.event_drift.portfolio import event_portfolio_returns
from experiments.market_signals.vol_targeting.data import equal_weight_returns

OUT = RESULTS_DIR / "event_drift"
HORIZON = 60
MIN_GAP = 21
GROUPS = [("abnormal_return", "up"), ("abnormal_return", "down"), ("abnormal_volume", "up")]
PRINT_H = [1, 2, 3, 5, 10, 21, 40, 60]
HOLDS = [5, 21]
COSTS_BPS = [0.0, 10.0]
QUANTILE_H = [5, 21, 60]
START_YEAR = int(os.environ.get("ED_START_YEAR", "1990"))
MAX_ASSETS = int(os.environ.get("ED_MAX_ASSETS", "0"))


def _load() -> tuple[dict[str, pd.DataFrame], pd.Series]:
    prices = load_long_history(start_year=START_YEAR)
    if MAX_ASSETS:
        prices = {k: prices[k] for k in sorted(prices)[:MAX_ASSETS]}
    market = equal_weight_returns(prices, min_names=min(30, max(1, len(prices) // 2)))
    return prices, market


def _scan_all(prices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for aid, df in prices.items():
        ev = scan_asset_events(df)
        if len(ev):
            ev.insert(0, "asset_id", aid)
            frames.append(ev)
    return pd.concat(frames, ignore_index=True)


def _caar_with_placebo(events: pd.DataFrame, excess: dict[str, pd.Series],
                       prices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    car = event_car_paths(excess, events, HORIZON)
    tbl = caar_table(car, events["date"])
    pl = placebo_events(events, prices)
    pl_car = event_car_paths(excess, pl, HORIZON)
    pl_tbl = caar_table(pl_car, pl["date"])[["h", "caar", "t"]]
    pl_tbl.columns = ["h", "placebo_caar", "placebo_t"]
    return tbl.merge(pl_tbl, on="h")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    prices, market = _load()
    excess = asset_excess(prices, market)
    print(f"[ed] {len(prices)} assets, market {market.index[0].date()}"
          f"..{market.index[-1].date()}", flush=True)

    raw = _scan_all(prices)
    events = dedupe_events(raw, MIN_GAP)
    summary = (events.groupby(["event_type", "direction"]).size().rename("n_dedup")
               .to_frame().join(raw.groupby(["event_type", "direction"]).size().rename("n_raw")))
    summary.to_csv(OUT / "events_summary.csv")
    print(f"[ed] events (raw -> dedup {MIN_GAP}d):\n{summary.to_string()}", flush=True)

    for etype, edir in GROUPS:
        grp = events[(events["event_type"] == etype)
                     & (events["direction"] == edir)].reset_index(drop=True)
        tbl = _caar_with_placebo(grp, excess, prices)
        tbl.to_csv(OUT / f"caar_{etype}_{edir}.csv", index=False)
        show = tbl[tbl["h"].isin(PRINT_H)]
        print(f"[ed] CAAR {etype}/{edir} (n={len(grp)}):\n"
              f"{show.round(4).to_string(index=False)}", flush=True)

    # 서프라이즈 크기 분위: |magnitude| 5분위별 CAAR(h) — 진짜 drift면 단조.
    q_rows = []
    for edir in ["up", "down"]:
        grp = events[(events["event_type"] == "abnormal_return")
                     & (events["direction"] == edir)].reset_index(drop=True)
        quintile = pd.qcut(grp["magnitude"].abs(), 5, labels=False) + 1
        car = event_car_paths(excess, grp, HORIZON)
        for q in range(1, 6):
            sub = car[quintile == q]
            dates = grp.loc[sub.index, "date"]
            tbl = caar_table(sub[QUANTILE_H], dates)
            for _, r in tbl.iterrows():
                q_rows.append({"direction": edir, "quintile": q, **r.to_dict()})
    qdf = pd.DataFrame(q_rows)
    qdf.to_csv(OUT / "magnitude_quintiles.csv", index=False)
    print(f"[ed] magnitude quintiles (h=21):\n"
          f"{qdf[qdf['h'] == 21].round(4).to_string(index=False)}", flush=True)

    # calendar-time 포트폴리오 (abnormal_return, 방향 부호).
    ar = events[events["event_type"] == "abnormal_return"].reset_index(drop=True)
    years = (market.index[-1] - market.index[0]).days / 365.25
    ppy = len(market) / years
    p_rows = []
    for hold in HOLDS:
        for cost in COSTS_BPS:
            ret, to = event_portfolio_returns(excess, ar, hold, cost)
            active = ret[ret != 0.0]
            p = perf_summary(ret, ppy)
            p_rows.append({"hold": hold, "cost_bps": cost, "sharpe": p["sharpe"],
                           "ann_ret": p["mean"] * ppy, "maxdd": p["maxdd"],
                           "avg_daily_turnover": to,
                           "pct_days_active": len(active) / max(len(ret), 1)})
    pdf = pd.DataFrame(p_rows)
    pdf.to_csv(OUT / "portfolio.csv", index=False)
    print(f"[ed] event portfolio (abnormal_return, signed):\n"
          f"{pdf.round(4).to_string(index=False)}", flush=True)
    print(f"[ed] wrote results to {OUT}", flush=True)


if __name__ == "__main__":
    main()

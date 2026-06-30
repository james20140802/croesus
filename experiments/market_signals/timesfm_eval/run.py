"""Experiment #1: does TimesFM beat naive baselines on index returns?

Evaluates directional hit rate + return RMSE skill vs random-walk and drift
baselines via rolling-origin out-of-sample. Honest "no" is a valid verdict.
"""
import datetime

import numpy as np
import pandas as pd

from experiments.market_signals.common import data
from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.timesfm_eval import metrics
from experiments.market_signals.timesfm_eval.model import TimesFMForecaster

CONTEXT = 512
HORIZONS = [1, 5, 21]
STEP = 21  # ~monthly origins to keep CPU runtime sane


def _rw(context, horizon):       # random walk: flat at last value
    return np.repeat(context[-1], horizon)


def _drift(context, horizon):    # extrapolate mean daily growth
    g = np.mean(np.diff(np.log(context)))
    return context[-1] * np.exp(g * np.arange(1, horizon + 1))


def _evaluate(name, series, forecaster):
    df = metrics.rolling_origin_eval(series, forecaster, CONTEXT, HORIZONS, STEP)
    out = []
    for h in HORIZONS:
        sub = df[df["h"] == h]
        rmse = float(np.sqrt(np.mean((sub["pred_return"] - sub["true_return"]) ** 2)))
        hit = metrics.directional_hit_rate(sub["true_return"].values,
                                           sub["pred_return"].values)
        out.append({"model": name, "h": h, "rmse": rmse, "hit_rate": hit,
                    "n": len(sub)})
    return pd.DataFrame(out)


def run():
    outdir = RESULTS_DIR / "timesfm_eval"
    outdir.mkdir(parents=True, exist_ok=True)
    tfm = TimesFMForecaster(context_len=CONTEXT)
    lines = ["# TimesFM (Experiment #1) — Findings\n"]
    all_rows = []
    for asset_id, ticker in data.INDICES.items():
        series = data.load_prices(asset_id, ticker,
                                  datetime.date(2005, 1, 1),
                                  datetime.date(2026, 6, 1))["adjusted_close"]
        res = pd.concat([
            _evaluate("timesfm", series, tfm),
            _evaluate("random_walk", series, _rw),
            _evaluate("drift", series, _drift),
        ])
        res.insert(0, "asset_id", asset_id)
        all_rows.append(res)
        lines.append(f"\n## {asset_id}\n")
        for h in HORIZONS:
            tf = res[(res.model == "timesfm") & (res.h == h)].iloc[0]
            rw = res[(res.model == "random_walk") & (res.h == h)].iloc[0]
            dr = res[(res.model == "drift") & (res.h == h)].iloc[0]
            ss = metrics.skill_score(tf["rmse"], rw["rmse"])
            lines.append(f"- h={h}: TimesFM hit={tf['hit_rate']:.2%}, "
                         f"drift baseline hit={dr['hit_rate']:.2%}, "
                         f"RMSE skill vs RW={ss:+.3f} "
                         f"({'beats' if ss > 0 else 'loses to'} random walk)")
    pd.concat(all_rows).to_csv(outdir / "skill_summary.csv", index=False)
    lines.append("\n## Verdict\n\nTimesFM is worth referencing only if directional "
                 "hit rate meaningfully EXCEEDS THE DRIFT BASELINE hit rate AND "
                 "RMSE skill vs random walk is >0 across horizons and both indices. "
                 "The correct directional bar is beating the drift baseline, not "
                 "merely exceeding 50%, because a trending index makes an "
                 "always-up / drift call score high at long horizons. Note: the "
                 "random-walk directional baseline is ~0% because it predicts zero "
                 "return, making it a meaningless directional bar. "
                 "If TimesFM hit rate does not beat the drift baseline and skill "
                 "<=0, it adds no usable signal at the index level.\n")
    (outdir / "FINDINGS.md").write_text("\n".join(lines))
    print(f"[timesfm] wrote {outdir}")


if __name__ == "__main__":
    run()

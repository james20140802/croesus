"""Experiment #3: magnitude + period of macro-event impact on the index.

Event-study CAAR over horizons == impulse response; magnitude = trough,
period = recovery horizon and half-life. Statsmodels local projection with
Newey-West bands is included as the rigorous cross-check.
"""
import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm

from experiments.market_signals.common import data
from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.event_impact import irf

HORIZONS = range(0, 61)  # T+0 .. T+60 trading days


def _load_events():
    csv = pd.read_csv(Path(__file__).parent / "events.csv")
    csv["date"] = pd.to_datetime(csv["date"]).dt.date
    return csv


def _local_projection(returns: pd.Series, event_dates, horizons):
    """Jordà LP: r(t->t+h) = a + b_h * Event_t; HAC (Newey-West) SE."""
    dates = returns.index
    ev = pd.Series(0.0, index=dates)
    for d in event_dates:
        pos = int(dates.searchsorted(pd.Timestamp(d)))
        if pos < len(dates):
            ev.iat[pos] = 1.0
    rows = []
    ret = returns.values
    for h in horizons:
        y = pd.Series(
            [np.sum(ret[i: i + h + 1]) if i + h < len(ret) else np.nan
             for i in range(len(ret))], index=dates)
        d = pd.DataFrame({"y": y, "ev": ev}).dropna()
        if d["ev"].sum() < 2:
            continue
        model = sm.OLS(d["y"], sm.add_constant(d["ev"]))
        res = model.fit(cov_type="HAC", cov_kwds={"maxlags": h + 1})
        rows.append({"h": h, "beta": res.params["ev"],
                     "se": res.bse["ev"]})
    return pd.DataFrame(rows)


def run():
    outdir = RESULTS_DIR / "event_impact"
    outdir.mkdir(parents=True, exist_ok=True)
    events = _load_events()
    summary = []
    lines = ["# Event Impact (Experiment #3) — Findings\n"]

    for asset_id, ticker in data.INDICES.items():
        prices = data.load_prices(asset_id, ticker,
                                  datetime.date(1985, 1, 1), datetime.date(2026, 6, 1))
        returns = prices["adjusted_close"].pct_change().dropna()
        lines.append(f"\n## {asset_id}\n")
        for cat in sorted(events["category"].unique()):
            ev_dates = events.loc[events["category"] == cat, "date"].tolist()
            curve = irf.caar_curve(returns, ev_dates, HORIZONS)
            if curve.empty:
                continue
            lp = _local_projection(returns, ev_dates, HORIZONS)
            mag = float(curve["caar"].min())
            rec = irf.recovery_horizon(curve)
            hl = irf.half_life(curve)
            curve.to_csv(outdir / f"{asset_id}_{cat}_caar.csv", index=False)
            if not lp.empty:
                lp.to_csv(outdir / f"{asset_id}_{cat}_lp.csv", index=False)

            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(curve["h"], curve["caar"], label="CAAR (event study)")
            ax.fill_between(curve["h"], curve["lo"], curve["hi"], alpha=0.2)
            if not lp.empty:
                ax.plot(lp["h"], lp["beta"], "--", label="Jordà LP β_h")
            ax.axhline(0, color="k", lw=0.5)
            ax.set_title(f"{asset_id} — {cat} (n={len(ev_dates)})")
            ax.set_xlabel("trading days after event"); ax.legend(fontsize=8)
            fig.tight_layout(); fig.savefig(outdir / f"{asset_id}_{cat}_irf.png", dpi=110)
            plt.close(fig)

            summary.append({"asset_id": asset_id, "category": cat,
                            "n_events": len(ev_dates), "magnitude": mag,
                            "recovery_days": rec, "half_life": hl})
            lines.append(f"- **{cat}** (n={len(ev_dates)}): magnitude={mag:.3%}, "
                         f"recovery={rec} days, half-life={hl}")

    pd.DataFrame(summary).to_csv(outdir / "summary.csv", index=False)
    lines.append("\n## Verdict\n\nCompare magnitude and recovery/half-life across "
                 "categories. CAAR and Jordà LP should broadly agree; where they "
                 "diverge, trust LP (it controls for overlap via HAC SE). Note "
                 "within-category dispersion (few events => wide bands).\n")
    (outdir / "FINDINGS.md").write_text("\n".join(lines))
    print(f"[event_impact] wrote {outdir}")


if __name__ == "__main__":
    run()

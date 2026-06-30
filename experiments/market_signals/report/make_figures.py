"""Generate publication-quality figures for the market-signals report.

Reads the result CSVs under results/ and writes clean figures to report/fig/.
Run from repo root:  python -m experiments.market_signals.report.make_figures
Figure axis labels are kept in English (robust across fonts); the LaTeX report
supplies Korean captions.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR

FIG_DIR = Path(__file__).resolve().parent / "fig"
FIG_DIR.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 150,
    "font.size": 10,
    "axes.titlesize": 11,
    "axes.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.unicode_minus": False,
})

C_TFM = "#2c6fbb"
C_DRIFT = "#e08a1e"
C_RW = "#9aa0a6"
C_CAAR = "#2c6fbb"
C_LP = "#c0392b"
INDICES = {"US_IDX_SP500": "S&P 500", "US_IDX_NASDAQ": "NASDAQ"}
HORIZONS = [1, 5, 21]


def _save(fig, name):
    path = FIG_DIR / name
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"[fig] {path}")


# ---------------------------------------------------------------- Experiment 1
def timesfm_figures():
    df = pd.read_csv(RESULTS_DIR / "timesfm_eval" / "skill_summary.csv")

    # (a) directional hit rate: TimesFM vs drift, per horizon, per index
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6), sharey=True)
    x = np.arange(len(HORIZONS))
    w = 0.38
    for ax, (aid, label) in zip(axes, INDICES.items()):
        sub = df[df.asset_id == aid]
        tfm = [sub[(sub.model == "timesfm") & (sub.h == h)].hit_rate.iloc[0] * 100 for h in HORIZONS]
        drift = [sub[(sub.model == "drift") & (sub.h == h)].hit_rate.iloc[0] * 100 for h in HORIZONS]
        ax.bar(x - w / 2, tfm, w, label="TimesFM", color=C_TFM)
        ax.bar(x + w / 2, drift, w, label="Drift baseline", color=C_DRIFT)
        ax.axhline(50, ls="--", lw=1, color="k", alpha=0.6)
        ax.text(len(HORIZONS) - 0.5, 50.6, "50% (chance)", ha="right", va="bottom", fontsize=8, alpha=0.7)
        for xi, (t, d) in enumerate(zip(tfm, drift)):
            ax.text(xi - w / 2, t + 0.6, f"{t:.0f}", ha="center", fontsize=7.5)
            ax.text(xi + w / 2, d + 0.6, f"{d:.0f}", ha="center", fontsize=7.5)
        ax.set_title(label)
        ax.set_xticks(x); ax.set_xticklabels([f"{h}d" for h in HORIZONS])
        ax.set_xlabel("forecast horizon")
        ax.set_ylim(40, 78)
    axes[0].set_ylabel("directional hit rate (%)")
    axes[0].legend(loc="upper left", fontsize=8, framealpha=0.9)
    _save(fig, "timesfm_hitrate.png")

    # (b) RMSE of return forecast: TimesFM vs RW vs drift
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4), sharey=False)
    for ax, (aid, label) in zip(axes, INDICES.items()):
        sub = df[df.asset_id == aid]
        for model, c, mk in [("timesfm", C_TFM, "o"), ("random_walk", C_RW, "s"), ("drift", C_DRIFT, "^")]:
            y = [sub[(sub.model == model) & (sub.h == h)].rmse.iloc[0] for h in HORIZONS]
            ax.plot(HORIZONS, y, marker=mk, color=c, label=model.replace("_", " "))
        ax.set_title(label)
        ax.set_xlabel("forecast horizon (days)")
        ax.set_xticks(HORIZONS)
    axes[0].set_ylabel("return RMSE (lower = better)")
    axes[1].legend(fontsize=8)
    _save(fig, "timesfm_rmse.png")


# ---------------------------------------------------------------- Experiment 2
def fourier_figures():
    # (a) clean spectra recomputed for S&P 500 across the 3 transforms
    try:
        import datetime
        from experiments.market_signals.common import data, detrend
        from experiments.market_signals.fourier import spectrum
        prices = data.load_prices("US_IDX_SP500", "^GSPC",
                                  datetime.date(1971, 1, 1), datetime.date(2026, 6, 1))["adjusted_close"]
        fig, axes = plt.subplots(3, 1, figsize=(8, 8))
        for ax, (name, fn) in zip(axes, detrend.TRANSFORMS.items()):
            x = fn(prices).dropna().values
            periods, power = spectrum.power_spectrum(x)
            band = spectrum.ar1_null_band(x, n_surrogate=200)
            n_sig = int((power > band).sum())
            ax.loglog(periods, power, lw=0.5, color=C_TFM, label="power spectrum")
            ax.loglog(periods, band, lw=1.0, color=C_LP, label="AR(1) 95% null")
            ax.set_title(f"{name}  —  {n_sig} peaks above null  (~{len(power)//20} expected by chance)")
            ax.set_xlabel("period (trading days)")
            ax.set_ylabel("power")
            ax.legend(fontsize=8, loc="lower left")
        _save(fig, "fourier_spectrum_sp500.png")
    except Exception as e:  # data/cache problem shouldn't sink the report
        print(f"[fig] spectrum recompute skipped: {e}")

    # (b) observed vs expected-by-chance peak counts
    observed = {
        "US_IDX_SP500": {"identity": 0, "demean_logret": 372, "detrend_linear": 202},
        "US_IDX_NASDAQ": {"identity": 0, "demean_logret": 367, "detrend_linear": 195},
    }
    expected = {"US_IDX_SP500": 349, "US_IDX_NASDAQ": 348}
    transforms = ["identity", "demean_logret", "detrend_linear"]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6), sharey=True)
    x = np.arange(len(transforms))
    for ax, (aid, label) in zip(axes, INDICES.items()):
        obs = [observed[aid][t] for t in transforms]
        ax.bar(x, obs, 0.55, color=C_TFM, label="observed significant peaks")
        ax.axhline(expected[aid], ls="--", lw=1.4, color=C_LP,
                   label=f"~{expected[aid]} expected by chance (5%)")
        for xi, v in enumerate(obs):
            ax.text(xi, v + 6, str(v), ha="center", fontsize=8)
        ax.set_title(label)
        ax.set_xticks(x); ax.set_xticklabels(transforms, rotation=12, fontsize=8)
        ax.set_ylim(0, 430)
    axes[0].set_ylabel("number of peaks above 95% null")
    axes[1].legend(fontsize=8)
    _save(fig, "fourier_peakcount.png")


# ---------------------------------------------------------------- Experiment 3
CATS = ["war", "pandemic", "tariff", "oil"]
# Figure labels stay ASCII (robust across matplotlib fonts); Korean lives in the LaTeX captions.
CAT_KO = {"war": "war", "pandemic": "pandemic", "tariff": "tariff", "oil": "oil"}


def event_figures():
    # (a) IRF panels for S&P 500: CAAR (+band) and Jordà LP beta
    fig, axes = plt.subplots(2, 2, figsize=(9.2, 6.4))
    for ax, cat in zip(axes.ravel(), CATS):
        caar = pd.read_csv(RESULTS_DIR / "event_impact" / f"US_IDX_SP500_{cat}_caar.csv")
        ax.plot(caar.h, caar.caar * 100, color=C_CAAR, lw=1.8, label="CAAR (event study)")
        ax.fill_between(caar.h, caar.lo * 100, caar.hi * 100, color=C_CAAR, alpha=0.18)
        lp_path = RESULTS_DIR / "event_impact" / f"US_IDX_SP500_{cat}_lp.csv"
        if lp_path.exists():
            lp = pd.read_csv(lp_path)
            ax.plot(lp.h, lp.beta * 100, ls="--", color=C_LP, lw=1.4, label="Jordà LP $\\beta_h$")
        ax.axhline(0, color="k", lw=0.6)
        ax.set_title(CAT_KO[cat])
        ax.set_xlabel("trading days after event")
        ax.set_ylabel("cumulative abnormal return (%)")
    axes[0, 0].legend(fontsize=8, loc="lower left")
    _save(fig, "event_irf_sp500.png")

    # (b) magnitude by category, both indices
    summ = pd.read_csv(RESULTS_DIR / "event_impact" / "summary.csv")
    fig, ax = plt.subplots(figsize=(8, 3.8))
    x = np.arange(len(CATS))
    w = 0.38
    for i, (aid, label) in enumerate(INDICES.items()):
        mags = []
        for cat in CATS:
            row = summ[(summ.asset_id == aid) & (summ.category == cat)]
            mags.append(row.magnitude.iloc[0] * 100 if len(row) else np.nan)
        off = (i - 0.5) * w
        bars = ax.bar(x + off, mags, w, label=label, color=[C_TFM, C_DRIFT][i])
        for xi, m in zip(x + off, mags):
            ax.text(xi, m - 1.6, f"{m:.1f}", ha="center", va="top", fontsize=7.5)
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xticks(x); ax.set_xticklabels([CAT_KO[c] for c in CATS])
    ax.set_ylabel("peak magnitude: trough CAAR (%)")
    ax.set_title("Shock magnitude by category")
    ax.legend(fontsize=8)
    _save(fig, "event_magnitude.png")


if __name__ == "__main__":
    timesfm_figures()
    fourier_figures()
    event_figures()
    print("done")

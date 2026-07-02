"""Experiment #2: do market indices contain genuine recurring cycles?

Runs the periodogram under every preprocessing transform (incl. identity, i.e.
no detrend) so the on/off comparison is explicit, tests peaks against an AR(1)
red-noise null, and writes plots + a FINDINGS.md.
"""
import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from experiments.market_signals.common import data, detrend
from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.fourier import spectrum

START = datetime.date(1971, 1, 1)
END = datetime.date(2026, 6, 1)


def run():
    outdir = RESULTS_DIR / "fourier"
    outdir.mkdir(parents=True, exist_ok=True)
    lines = ["# Fourier (Experiment #2) — Findings\n"]

    for asset_id, ticker in data.INDICES.items():
        prices = data.load_prices(asset_id, ticker, START, END)["adjusted_close"]
        fig, axes = plt.subplots(len(detrend.TRANSFORMS), 1, figsize=(10, 9), sharex=False, squeeze=False)
        axes = axes[:, 0]  # flatten to 1-D array
        lines.append(f"\n## {asset_id}\n")
        for ax, (name, fn) in zip(axes, detrend.TRANSFORMS.items()):
            x = fn(prices).dropna().values
            periods, power = spectrum.power_spectrum(x)
            band = spectrum.ar1_null_band(x, n_surrogate=200)
            peaks = spectrum.significant_peaks(periods, power, band)
            peaks = peaks[(peaks["period"] >= 2) & (peaks["period"] <= 2000)]
            ax.loglog(periods, power, lw=0.6, label="power")
            ax.loglog(periods, band, lw=0.8, color="red", label="AR(1) 95%")
            ax.set_title(f"{name}: {len(peaks)} peaks above red-noise")
            ax.set_xlabel("period (trading days)")
            ax.legend(fontsize=7)
            peaks.head(10).to_csv(outdir / f"{asset_id}_{name}_peaks.csv", index=False)
            top = ", ".join(f"{p:.0f}d" for p in peaks["period"].head(5)) or "none"
            expected_fp = len(power) // 20
            lines.append(f"- **{name}**: {len(peaks)} significant peaks (≈{expected_fp} expected by chance at 95%); top: {top}")
        fig.tight_layout()
        fig.savefig(outdir / f"{asset_id}_spectrum.png", dpi=110)
        plt.close(fig)

    lines.append(
        "\n## Verdict\n\nCompare the `identity` (no-detrend) panel against the "
        "detrended panels: raw log-price spectra are dominated by low-frequency "
        "trend energy, which collapses once drift is removed. Judge whether any "
        "peak survives the AR(1) red-noise null across both indices and multiple "
        "transforms — only those are candidate real cycles; the rest are "
        "consistent with red noise. Note: at the 95% per-frequency threshold the "
        "null AR(1) process produces roughly 5% of bins as false peaks, so any "
        "peak count near or below the expected-by-chance number is consistent "
        "with pure red noise — only peaks that survive across both indices and "
        "multiple transforms are candidate real cycles.\n")
    (outdir / "FINDINGS.md").write_text("\n".join(lines))
    print(f"[fourier] wrote {outdir}")


if __name__ == "__main__":
    run()

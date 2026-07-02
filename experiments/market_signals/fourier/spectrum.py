"""Periodogram + AR(1) red-noise significance for index returns."""
import numpy as np
import pandas as pd


def power_spectrum(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    n = len(x)
    fft = np.fft.rfft(x)
    power = (np.abs(fft) ** 2) / n
    freqs = np.fft.rfftfreq(n, d=1.0)
    # drop DC (freq 0) — undefined period
    freqs, power = freqs[1:], power[1:]
    periods = 1.0 / freqs
    return periods, power


def _ar1_surrogate(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    x = x - x.mean()
    n = len(x)
    phi = np.corrcoef(x[:-1], x[1:])[0, 1]
    phi = float(np.clip(phi, -0.999, 0.999))
    sigma = np.std(x) * np.sqrt(1 - phi ** 2)
    out = np.empty(n)
    out[0] = x[0]
    noise = rng.standard_normal(n) * sigma
    for i in range(1, n):
        out[i] = phi * out[i - 1] + noise[i]
    return out


def ar1_null_band(x: np.ndarray, n_surrogate: int = 500, pct: float = 95.0) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    rng = np.random.default_rng(0)
    _, ref_power = power_spectrum(x)
    sims = np.empty((n_surrogate, len(ref_power)))
    for k in range(n_surrogate):
        s = _ar1_surrogate(x, rng)
        _, p = power_spectrum(s)
        sims[k] = p
    return np.percentile(sims, pct, axis=0)


def significant_peaks(periods, power, band):
    mask = power > band
    return pd.DataFrame({
        "period": periods[mask],
        "power": power[mask],
        "threshold": band[mask],
    }).sort_values("power", ascending=False).reset_index(drop=True)

import numpy as np

from experiments.market_signals.fourier import spectrum


def test_power_spectrum_finds_injected_period():
    n = 1024
    t = np.arange(n)
    period = 20.0
    x = np.sin(2 * np.pi * t / period) + 0.01 * np.random.default_rng(0).standard_normal(n)
    periods, power = spectrum.power_spectrum(x)
    peak_period = periods[np.argmax(power)]
    assert abs(peak_period - period) < 1.0


def test_ar1_null_band_shape_matches():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(512)
    periods, power = spectrum.power_spectrum(x)
    band = spectrum.ar1_null_band(x, n_surrogate=50)
    assert band.shape == power.shape


def test_pure_noise_has_few_significant_peaks():
    rng = np.random.default_rng(2)
    x = rng.standard_normal(1024)  # white noise: no real cycles
    periods, power = spectrum.power_spectrum(x)
    band = spectrum.ar1_null_band(x, n_surrogate=200, pct=99.0)
    peaks = spectrum.significant_peaks(periods, power, band)
    # at 99% a handful of false positives is fine; should be a small fraction
    assert len(peaks) < 0.05 * len(power)

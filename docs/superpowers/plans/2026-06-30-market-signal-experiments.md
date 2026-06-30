# Market Signal Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build three throwaway exploratory experiments (TimesFM forecast skill, Fourier spectral cycles, macro-event impulse response) under `experiments/market_signals/`, each producing CSV/PNG artifacts and a `FINDINGS.md` verdict.

**Architecture:** A small shared layer (`common/data.py` price loader over the existing DuckDB cache, `common/detrend.py` preprocessing transforms incl. an identity no-op for on/off comparison) feeds three independent runners. Pure-math modules are built test-first; model inference and plotting are verified by smoke runs. Heavy deps (`timesfm`, `torch`, `statsmodels`, `scipy`) live in an experiment-local `requirements.txt` and never touch the main `pyproject.toml`.

**Tech Stack:** Python 3.10, numpy, pandas, scipy, statsmodels, matplotlib, yfinance, duckdb, timesfm + torch (CPU).

## Global Constraints

- Python `>=3.10` (match repo).
- Heavy/experimental deps go in `experiments/market_signals/requirements.txt` ONLY — never edit the root `pyproject.toml`.
- Reuse the existing DuckDB file `storage/croesus.duckdb` and its `prices_daily` table; do NOT create a second database. Share the cache by reading/writing the same table, not by importing `events_impact` modules (that package uses top-level `from config import ...` imports that only resolve when its own dir is on `sys.path`).
- Asset ids / tickers: S&P 500 = `US_IDX_SP500` / `^GSPC`; NASDAQ Composite = `US_IDX_NASDAQ` / `^IXIC`.
- Index-level daily data only. No intraday, no single-stock studies, no integration into the main `croesus/` package.
- Every experiment writes an honest `FINDINGS.md` — "this technique adds no value here" is a valid, expected result.
- All artifacts go under `experiments/market_signals/results/` which must be gitignored.
- Tests run with `pytest` from the repo root.

---

### Task 1: Scaffold + shared price loader

**Files:**
- Create: `experiments/market_signals/__init__.py` (empty)
- Create: `experiments/market_signals/common/__init__.py` (empty)
- Create: `experiments/market_signals/requirements.txt`
- Create: `experiments/market_signals/.gitignore`
- Create: `experiments/market_signals/common/config.py`
- Create: `experiments/market_signals/common/data.py`
- Test: `experiments/market_signals/tests/__init__.py` (empty), `experiments/market_signals/tests/test_data.py`

**Interfaces:**
- Produces: `common.config.DB_PATH: Path`, `RESULTS_DIR: Path`, `EXP_DIR: Path`.
- Produces: `common.data.INDICES: dict[str, str]` mapping `asset_id -> ticker` (`{"US_IDX_SP500": "^GSPC", "US_IDX_NASDAQ": "^IXIC"}`).
- Produces: `common.data.load_prices(asset_id: str, ticker: str, start: datetime.date, end: datetime.date) -> pd.DataFrame` — DatetimeIndex named `date`, single column `adjusted_close`, sorted ascending. DuckDB read-through cache against `prices_daily`.

- [ ] **Step 1: Create scaffold files**

`experiments/market_signals/requirements.txt`:
```
# Isolated heavy deps for the market_signals experiments.
# Install into a dedicated venv; do NOT add these to the root pyproject.toml.
numpy>=1.24
pandas>=2.0
scipy>=1.11
statsmodels>=0.14
matplotlib>=3.7
yfinance>=0.2
duckdb>=1.0
# Experiment #1 only (large download). Pin to a known-good release.
timesfm==1.2.0
torch>=2.2
```

`experiments/market_signals/.gitignore`:
```
results/
__pycache__/
*.pyc
```

`experiments/market_signals/common/config.py`:
```python
from pathlib import Path

# common/config.py -> parents[2] == experiments/market_signals
EXP_DIR = Path(__file__).resolve().parents[1]
# repo root is three levels above experiments/market_signals/common
REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "storage" / "croesus.duckdb"
RESULTS_DIR = EXP_DIR / "results"
```

Create the four empty `__init__.py` files (`market_signals/`, `common/`, `tests/`) and an empty `experiments/market_signals/tests/__init__.py`.

- [ ] **Step 2: Write the failing test**

`experiments/market_signals/tests/test_data.py`:
```python
import datetime

import pandas as pd

from experiments.market_signals.common import data


def test_indices_registry():
    assert data.INDICES["US_IDX_SP500"] == "^GSPC"
    assert data.INDICES["US_IDX_NASDAQ"] == "^IXIC"


def test_load_prices_returns_sorted_adjusted_close():
    df = data.load_prices(
        "US_IDX_SP500", "^GSPC",
        datetime.date(2020, 1, 1), datetime.date(2020, 3, 1),
    )
    assert list(df.columns) == ["adjusted_close"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert len(df) > 20  # ~40 trading days in two months
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest experiments/market_signals/tests/test_data.py -v`
Expected: FAIL — `ModuleNotFoundError: experiments.market_signals.common.data`.

- [ ] **Step 4: Implement `common/data.py`**

```python
"""Daily index prices via a DuckDB read-through cache.

Shares storage/croesus.duckdb and the prices_daily table with the rest of the
repo. Self-contained (no cross-package imports) so it runs from the repo root.
"""
import datetime
import sys

import duckdb
import pandas as pd
import yfinance as yf

from experiments.market_signals.common.config import DB_PATH

INDICES = {"US_IDX_SP500": "^GSPC", "US_IDX_NASDAQ": "^IXIC"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices_daily (
    asset_id TEXT, date DATE, open DOUBLE, high DOUBLE, low DOUBLE,
    close DOUBLE, adjusted_close DOUBLE, volume BIGINT, source TEXT,
    PRIMARY KEY (asset_id, date)
)
"""


def _connect() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    conn.execute(_SCHEMA)
    return conn


def _fetch(ticker: str, start: datetime.date, end: datetime.date) -> pd.DataFrame:
    raw = yf.download(
        ticker, start=str(start), end=str(end + datetime.timedelta(days=1)),
        auto_adjust=False, progress=False,
    )
    if raw.empty:
        return raw
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.index = pd.to_datetime(raw.index).date
    return raw


def load_prices(asset_id: str, ticker: str,
                start: datetime.date, end: datetime.date) -> pd.DataFrame:
    conn = _connect()
    cached = conn.execute(
        "SELECT date FROM prices_daily WHERE asset_id=? AND date BETWEEN ? AND ?",
        [asset_id, start, end],
    ).fetchdf()
    have = set(cached["date"].dt.date if hasattr(cached["date"], "dt") else cached["date"])

    covered = bool(have) and start >= min(have) and end <= max(have)
    if not covered:
        print(f"[data] fetching {ticker} {start}->{end}", file=sys.stderr)
        raw = _fetch(ticker, start, end)
        if not raw.empty:
            adj = "Adj Close" if "Adj Close" in raw.columns else "Close"
            rows = [(
                asset_id, dt,
                float(r.get("Open", float("nan"))), float(r.get("High", float("nan"))),
                float(r.get("Low", float("nan"))), float(r.get("Close", float("nan"))),
                float(r.get(adj, float("nan"))), int(r.get("Volume", 0) or 0), "yfinance",
            ) for dt, r in raw.iterrows()]
            conn.executemany(
                "INSERT OR REPLACE INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)", rows)

    out = conn.execute(
        """SELECT date, adjusted_close FROM prices_daily
           WHERE asset_id=? AND date BETWEEN ? AND ? ORDER BY date""",
        [asset_id, start, end],
    ).fetchdf()
    conn.close()
    if out.empty:
        raise ValueError(f"No price data for {asset_id} in {start}:{end}")
    out["date"] = pd.to_datetime(out["date"])
    return out.set_index("date").sort_index()
```

- [ ] **Step 5: Run test to verify it passes** (requires network on first run; afterwards served from cache)

Run: `python -m pytest experiments/market_signals/tests/test_data.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add experiments/market_signals/__init__.py experiments/market_signals/common experiments/market_signals/tests experiments/market_signals/requirements.txt experiments/market_signals/.gitignore
git commit -m "✨ feat: scaffold market_signals experiments + shared price loader"
```

---

### Task 2: Shared preprocessing transforms (`common/detrend.py`)

**Files:**
- Create: `experiments/market_signals/common/detrend.py`
- Test: `experiments/market_signals/tests/test_detrend.py`

**Interfaces:**
- Consumes: nothing (pure numpy/pandas).
- Produces, each taking and returning a `pd.Series` (DatetimeIndex preserved where applicable):
  - `identity(price: pd.Series) -> pd.Series` — returns `log(price)` unchanged otherwise (no-op detrend baseline for the on/off comparison; works on log price so it is comparable to the other transforms' domain).
  - `log_returns(price: pd.Series) -> pd.Series` — `diff(log(price))`, first value dropped.
  - `demean_drift(series: pd.Series) -> pd.Series` — `series - series.mean()`.
  - `detrend_logprice(price: pd.Series, kind: str = "linear") -> pd.Series` — fit and subtract a `"linear"` or `"exp"` trend from `log(price)`, return residual.
  - `TRANSFORMS: dict[str, callable]` — named registry used by experiments to loop over `{"identity", "demean_logret", "detrend_linear"}` where `demean_logret(price) = demean_drift(log_returns(price))`.

- [ ] **Step 1: Write the failing tests**

`experiments/market_signals/tests/test_detrend.py`:
```python
import numpy as np
import pandas as pd
import pytest

from experiments.market_signals.common import detrend


@pytest.fixture
def price():
    # pure exponential growth: log price is exactly linear
    idx = pd.date_range("2000-01-01", periods=100, freq="D")
    return pd.Series(100 * np.exp(0.001 * np.arange(100)), index=idx)


def test_log_returns_constant_for_exponential(price):
    r = detrend.log_returns(price)
    assert len(r) == len(price) - 1
    assert np.allclose(r.values, 0.001, atol=1e-9)


def test_demean_drift_zero_mean(price):
    r = detrend.demean_drift(detrend.log_returns(price))
    assert abs(r.mean()) < 1e-12


def test_detrend_logprice_linear_removes_trend(price):
    resid = detrend.detrend_logprice(price, kind="linear")
    # exact linear log-price => residual ~ 0
    assert np.allclose(resid.values, 0.0, atol=1e-8)


def test_identity_is_log_price(price):
    out = detrend.identity(price)
    assert np.allclose(out.values, np.log(price.values))


def test_transforms_registry_keys():
    assert set(detrend.TRANSFORMS) == {"identity", "demean_logret", "detrend_linear"}
    # every transform maps a price series to a finite series
    idx = pd.date_range("2000-01-01", periods=50, freq="D")
    p = pd.Series(np.linspace(100, 150, 50), index=idx)
    for name, fn in detrend.TRANSFORMS.items():
        out = fn(p)
        assert np.isfinite(out.values).all()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest experiments/market_signals/tests/test_detrend.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `common/detrend.py`**

```python
"""Shared preprocessing transforms for the market_signals experiments.

The point of `identity` is the preprocessing on/off comparison: experiments
loop over TRANSFORMS so the same analysis runs with and without detrending.
"""
import numpy as np
import pandas as pd


def identity(price: pd.Series) -> pd.Series:
    """No-op detrend baseline. Works in log space for comparability."""
    return pd.Series(np.log(price.values), index=price.index, name="identity")


def log_returns(price: pd.Series) -> pd.Series:
    r = np.diff(np.log(price.values))
    return pd.Series(r, index=price.index[1:], name="log_returns")


def demean_drift(series: pd.Series) -> pd.Series:
    return series - series.mean()


def detrend_logprice(price: pd.Series, kind: str = "linear") -> pd.Series:
    logp = np.log(price.values)
    x = np.arange(len(logp), dtype=float)
    if kind == "linear":
        coef = np.polyfit(x, logp, 1)
        trend = np.polyval(coef, x)
    elif kind == "exp":
        # exponential trend in price == linear trend in log price fit by least
        # squares on log scale, then re-expressed; residual is in log space.
        coef = np.polyfit(x, logp, 1)
        trend = np.polyval(coef, x)
    else:
        raise ValueError(f"unknown kind: {kind}")
    return pd.Series(logp - trend, index=price.index, name=f"detrend_{kind}")


def _demean_logret(price: pd.Series) -> pd.Series:
    return demean_drift(log_returns(price))


TRANSFORMS = {
    "identity": identity,
    "demean_logret": _demean_logret,
    "detrend_linear": lambda p: detrend_logprice(p, kind="linear"),
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest experiments/market_signals/tests/test_detrend.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add experiments/market_signals/common/detrend.py experiments/market_signals/tests/test_detrend.py
git commit -m "✨ feat: shared detrend transforms with identity no-op for on/off comparison"
```

---

### Task 3: Experiment #2 — Fourier spectral analysis

**Files:**
- Create: `experiments/market_signals/fourier/__init__.py` (empty)
- Create: `experiments/market_signals/fourier/spectrum.py`
- Create: `experiments/market_signals/fourier/run.py`
- Test: `experiments/market_signals/tests/test_spectrum.py`

**Interfaces:**
- Consumes: `common.detrend.TRANSFORMS`, `common.data.load_prices`, `common.config.RESULTS_DIR`.
- Produces:
  - `spectrum.power_spectrum(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]` — returns `(periods, power)` where `periods` is in samples (trading days) per cycle and `power` is the periodogram, DC term dropped.
  - `spectrum.ar1_null_band(x: np.ndarray, n_surrogate: int = 500, pct: float = 95.0) -> np.ndarray` — power threshold per frequency from AR(1) red-noise surrogates (matched lag-1 autocorr + variance), aligned to `power_spectrum`'s frequency grid.
  - `spectrum.significant_peaks(periods, power, band) -> pd.DataFrame[period, power, threshold]` — rows where `power > band`.

- [ ] **Step 1: Write the failing tests**

`experiments/market_signals/tests/test_spectrum.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest experiments/market_signals/tests/test_spectrum.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `fourier/spectrum.py`**

```python
"""Periodogram + AR(1) red-noise significance for index returns."""
import numpy as np


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
    import pandas as pd
    mask = power > band
    return pd.DataFrame({
        "period": periods[mask],
        "power": power[mask],
        "threshold": band[mask],
    }).sort_values("power", ascending=False).reset_index(drop=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest experiments/market_signals/tests/test_spectrum.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the runner `fourier/run.py`**

```python
"""Experiment #2: do market indices contain genuine recurring cycles?

Runs the periodogram under every preprocessing transform (incl. identity, i.e.
no detrend) so the on/off comparison is explicit, tests peaks against an AR(1)
red-noise null, and writes plots + a FINDINGS.md.
"""
import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

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
        fig, axes = plt.subplots(len(detrend.TRANSFORMS), 1, figsize=(10, 9), sharex=False)
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
            lines.append(f"- **{name}**: {len(peaks)} significant peaks; top: {top}")
        fig.tight_layout()
        fig.savefig(outdir / f"{asset_id}_spectrum.png", dpi=110)
        plt.close(fig)

    lines.append(
        "\n## Verdict\n\nCompare the `identity` (no-detrend) panel against the "
        "detrended panels: raw log-price spectra are dominated by low-frequency "
        "trend energy, which collapses once drift is removed. Judge whether any "
        "peak survives the AR(1) red-noise null across both indices and multiple "
        "transforms — only those are candidate real cycles; the rest are "
        "consistent with red noise.\n")
    (outdir / "FINDINGS.md").write_text("\n".join(lines))
    print(f"[fourier] wrote {outdir}")


if __name__ == "__main__":
    run()
```

- [ ] **Step 6: Smoke-run the experiment**

Run: `python -m experiments.market_signals.fourier.run`
Expected: prints `[fourier] wrote .../results/fourier`; that dir contains `*_spectrum.png`, `*_peaks.csv`, and `FINDINGS.md`. Open `FINDINGS.md` and confirm peak counts differ between `identity` and detrended transforms.

- [ ] **Step 7: Commit**

```bash
git add experiments/market_signals/fourier experiments/market_signals/tests/test_spectrum.py
git commit -m "✨ feat: Fourier experiment with AR(1) significance and detrend on/off comparison"
```

---

### Task 4: Experiment #3 — Event impact (CAAR + local-projection IRF)

**Files:**
- Create: `experiments/market_signals/event_impact/__init__.py` (empty)
- Create: `experiments/market_signals/event_impact/events.csv`
- Create: `experiments/market_signals/event_impact/irf.py`
- Create: `experiments/market_signals/event_impact/run.py`
- Test: `experiments/market_signals/tests/test_irf.py`

**Interfaces:**
- Consumes: `common.data.load_prices`, `common.config.RESULTS_DIR`.
- Produces:
  - `irf.caar_curve(returns: pd.Series, event_dates: list, horizons: range, est_window: tuple = (-31, -2)) -> pd.DataFrame[h, caar, se, lo, hi]` — mean cumulative abnormal return at each horizon `h>=0` with cross-sectional 95% band. Abnormal return = actual − mean(estimation-window return).
  - `irf.recovery_horizon(curve: pd.DataFrame) -> int | None` — first `h>0` where the band re-contains 0 after the peak; `None` if never.
  - `irf.half_life(curve: pd.DataFrame) -> float | None` — `ln(0.5)/ln(rho)` from an AR(1) fit `caar_h ~ rho * caar_{h-1}` past the peak; `None` if `rho` not in `(0,1)`.

- [ ] **Step 1: Create the curated event CSV**

`experiments/market_signals/event_impact/events.csv`:
```csv
date,category,magnitude,scope,metadata
2001-09-11,war,,US,"{""name"":""9/11""}"
1991-01-17,war,,Gulf,"{""name"":""Gulf War start""}"
2022-02-24,war,,Russia-Ukraine,"{""name"":""RU invasion""}"
2026-03-01,war,,Iran,"{""name"":""Iran war 2026""}"
2020-02-24,pandemic,,Global,"{""name"":""COVID crash onset""}"
2018-07-06,tariff,34,US-China,"{""name"":""first China tariffs""}"
2019-05-10,tariff,25,US-China,"{""name"":""tariff escalation""}"
2025-04-02,tariff,,US-broad,"{""name"":""2025 tariffs""}"
1990-08-02,oil,,Gulf,"{""name"":""Gulf oil spike""}"
2008-06-01,oil,,Global,"{""name"":""2008 oil peak""}"
2022-03-01,oil,,Russia-Ukraine,"{""name"":""2022 oil spike""}"
2026-03-01,oil,,Iran,"{""name"":""Iran oil shock 2026""}"
```
(The 2026-03 Iran event intentionally appears in BOTH `war` and `oil` — it is simultaneously a conflict and an oil shock. Adjust the exact day during implementation if a more precise onset date is known.)

- [ ] **Step 2: Write the failing tests**

`experiments/market_signals/tests/test_irf.py`:
```python
import numpy as np
import pandas as pd

from experiments.market_signals.event_impact import irf


def _series_with_shock(shock_at, drop=-0.10, recover_days=10):
    idx = pd.date_range("2000-01-01", periods=200, freq="B")
    r = pd.Series(0.0, index=idx)
    pos = idx.get_loc(pd.Timestamp(shock_at))
    r.iloc[pos] = drop                      # impulse down
    for k in range(1, recover_days + 1):    # gradual recovery
        r.iloc[pos + k] = -drop / recover_days
    return r


def test_caar_curve_columns_and_peak():
    r = _series_with_shock("2000-03-01")
    curve = irf.caar_curve(r, [pd.Timestamp("2000-03-01").date()], range(0, 20))
    assert {"h", "caar", "se", "lo", "hi"}.issubset(curve.columns)
    # cumulative abnormal return troughs near the -0.10 impulse
    assert curve["caar"].min() < -0.05


def test_recovery_horizon_detects_return_to_zero():
    r = _series_with_shock("2000-03-01", drop=-0.10, recover_days=8)
    curve = irf.caar_curve(r, [pd.Timestamp("2000-03-01").date()], range(0, 30))
    h = irf.recovery_horizon(curve)
    assert h is None or h > 0


def test_half_life_positive_for_decaying_curve():
    curve = pd.DataFrame({
        "h": range(0, 10),
        "caar": [-0.1 * (0.7 ** k) for k in range(10)],
        "se": [0.0] * 10, "lo": [0.0] * 10, "hi": [0.0] * 10,
    })
    hl = irf.half_life(curve)
    assert hl is not None and hl > 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest experiments/market_signals/tests/test_irf.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement `event_impact/irf.py`**

```python
"""Impulse-response estimators for macro-event impact.

caar_curve: event-study CAAR over horizons (the simple IRF).
recovery_horizon + half_life: turn the IRF into magnitude/period numbers.
"""
import datetime

import numpy as np
import pandas as pd


def caar_curve(returns: pd.Series, event_dates, horizons,
               est_window: tuple = (-31, -2)) -> pd.DataFrame:
    dates = returns.index
    est_lo, est_hi = est_window
    per_event = {h: [] for h in horizons}
    for ev in event_dates:
        ev_ts = pd.Timestamp(ev)
        pos = int(dates.searchsorted(ev_ts))
        if pos >= len(dates) or pos + est_lo < 0 or pos + max(horizons) >= len(dates):
            continue
        est = returns.iloc[pos + est_lo: pos + est_hi + 1].dropna()
        if len(est) < 0.8 * (est_hi - est_lo + 1):
            continue
        mu = float(est.mean())
        car = 0.0
        for h in horizons:
            ar = float(returns.iloc[pos + h]) - mu
            car += ar
            per_event[h].append(car)
    rows = []
    for h in horizons:
        vals = np.array(per_event[h], dtype=float)
        if len(vals) == 0:
            continue
        mean = vals.mean()
        se = vals.std(ddof=1) / np.sqrt(len(vals)) if len(vals) > 1 else 0.0
        rows.append({"h": h, "caar": mean, "se": se,
                     "lo": mean - 1.96 * se, "hi": mean + 1.96 * se})
    return pd.DataFrame(rows)


def recovery_horizon(curve: pd.DataFrame):
    if curve.empty:
        return None
    trough_idx = curve["caar"].idxmin()
    after = curve.loc[trough_idx:]
    for _, row in after.iterrows():
        if row["h"] > 0 and row["lo"] <= 0 <= row["hi"]:
            return int(row["h"])
    return None


def half_life(curve: pd.DataFrame):
    if curve.empty:
        return None
    trough_idx = curve["caar"].idxmin()
    seg = curve.loc[trough_idx:, "caar"].values
    if len(seg) < 3:
        return None
    a, b = seg[:-1], seg[1:]
    denom = float(np.dot(a, a))
    if denom == 0:
        return None
    rho = float(np.dot(a, b) / denom)
    if not (0 < rho < 1):
        return None
    return float(np.log(0.5) / np.log(rho))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest experiments/market_signals/tests/test_irf.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Write the runner `event_impact/run.py`**

```python
"""Experiment #3: magnitude + period of macro-event impact on the index.

Event-study CAAR over horizons == impulse response; magnitude = trough,
period = recovery horizon and half-life. Statsmodels local projection with
Newey-West bands is included as the rigorous cross-check.
"""
import datetime
import json

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
    csv = pd.read_csv(__file__.replace("run.py", "events.csv"))
    csv["date"] = pd.to_datetime(csv["date"]).dt.date
    return csv


def _local_projection(returns: pd.Series, event_dates, horizons):
    """Jordà LP: r(t->t+h) = a + b_h * Event_t; HAC (Newey-West) SE."""
    dates = returns.index
    ev = pd.Series(0.0, index=dates)
    for d in event_dates:
        pos = int(dates.searchsorted(pd.Timestamp(d)))
        if pos < len(dates):
            ev.iloc[pos] = 1.0
    rows = []
    logret = returns.values
    for h in horizons:
        y = pd.Series(
            [np.sum(logret[i: i + h + 1]) if i + h < len(logret) else np.nan
             for i in range(len(logret))], index=dates)
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
```

- [ ] **Step 7: Smoke-run the experiment**

Run: `python -m experiments.market_signals.event_impact.run`
Expected: prints `[event_impact] wrote .../results/event_impact`; dir contains `*_irf.png`, `*_caar.csv`, `summary.csv`, `FINDINGS.md`. Confirm `summary.csv` has rows for war/pandemic/tariff/oil with magnitude and recovery values.

- [ ] **Step 8: Commit**

```bash
git add experiments/market_signals/event_impact experiments/market_signals/tests/test_irf.py
git commit -m "✨ feat: event-impact experiment (CAAR + Jordà local-projection IRF)"
```

---

### Task 5: Experiment #1 — TimesFM forecast skill evaluation

**Files:**
- Create: `experiments/market_signals/timesfm_eval/__init__.py` (empty)
- Create: `experiments/market_signals/timesfm_eval/metrics.py`
- Create: `experiments/market_signals/timesfm_eval/model.py`
- Create: `experiments/market_signals/timesfm_eval/run.py`
- Test: `experiments/market_signals/tests/test_metrics.py`

**Interfaces:**
- Consumes: `common.data.load_prices`, `common.config.RESULTS_DIR`.
- Produces:
  - `metrics.directional_hit_rate(y_true: np.ndarray, y_pred: np.ndarray) -> float` — fraction where `sign(y_pred) == sign(y_true)`.
  - `metrics.skill_score(err_model: float, err_baseline: float) -> float` — `1 - err_model/err_baseline` (>0 means beats baseline).
  - `metrics.rolling_origin_eval(series: pd.Series, forecaster, context_len: int, horizons: list[int], step: int) -> pd.DataFrame` — at each origin, calls `forecaster(context: np.ndarray, horizon: int) -> np.ndarray` (length-`horizon` predicted **price levels**), records realized vs predicted return over each horizon. Returns long DataFrame `[origin, h, pred_return, true_return]`.
  - `model.TimesFMForecaster` — wraps the installed `timesfm` package behind the same `forecaster(context, horizon)` call signature; isolates ALL version-specific API there.

- [ ] **Step 1: Write the failing tests** (metrics are model-free, fully testable with a dummy forecaster)

`experiments/market_signals/tests/test_metrics.py`:
```python
import numpy as np
import pandas as pd

from experiments.market_signals.timesfm_eval import metrics


def test_directional_hit_rate():
    y = np.array([0.01, -0.02, 0.03, -0.01])
    p = np.array([0.02, -0.01, -0.05, -0.02])  # 3 of 4 signs match
    assert metrics.directional_hit_rate(y, p) == 0.75


def test_skill_score_beats_baseline():
    assert metrics.skill_score(0.5, 1.0) == 0.5      # half the error
    assert metrics.skill_score(2.0, 1.0) == -1.0     # worse than baseline


def test_rolling_origin_eval_with_persistence_forecaster():
    idx = pd.date_range("2000-01-01", periods=120, freq="B")
    series = pd.Series(100 + np.cumsum(np.ones(120)), index=idx)  # +1/day

    def persistence(context, horizon):
        # predict flat = last value repeated => predicts zero return
        return np.repeat(context[-1], horizon)

    df = metrics.rolling_origin_eval(series, persistence,
                                     context_len=30, horizons=[1, 5], step=10)
    assert set(df["h"].unique()) == {1, 5}
    # persistence predicts 0 return; true return is positive here
    assert (df["pred_return"].abs() < 1e-9).all()
    assert (df["true_return"] > 0).all()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest experiments/market_signals/tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `timesfm_eval/metrics.py`**

```python
"""Model-free forecast-evaluation harness (testable without TimesFM)."""
import numpy as np
import pandas as pd


def directional_hit_rate(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    return float(np.mean(np.sign(y_true) == np.sign(y_pred)))


def skill_score(err_model: float, err_baseline: float) -> float:
    if err_baseline == 0:
        return float("nan")
    return 1.0 - err_model / err_baseline


def rolling_origin_eval(series: pd.Series, forecaster, context_len: int,
                        horizons, step: int) -> pd.DataFrame:
    vals = series.values.astype(float)
    n = len(vals)
    hmax = max(horizons)
    rows = []
    for origin in range(context_len, n - hmax, step):
        context = vals[origin - context_len: origin]
        preds = forecaster(context, hmax)
        last = context[-1]
        for h in horizons:
            pred_price = preds[h - 1]
            true_price = vals[origin + h - 1]
            rows.append({
                "origin": origin, "h": h,
                "pred_return": pred_price / last - 1.0,
                "true_return": true_price / last - 1.0,
            })
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest experiments/market_signals/tests/test_metrics.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit the testable core**

```bash
git add experiments/market_signals/timesfm_eval/__init__.py experiments/market_signals/timesfm_eval/metrics.py experiments/market_signals/tests/test_metrics.py
git commit -m "✨ feat: TimesFM eval harness (rolling-origin metrics, model-free)"
```

- [ ] **Step 6: Install heavy deps and verify the TimesFM API surface**

Create a dedicated venv and install (this downloads torch + model weights, may take a while):
```bash
python -m venv .venv-timesfm
source .venv-timesfm/bin/activate
pip install -r experiments/market_signals/requirements.txt
python -c "import timesfm; print(timesfm.__version__); print([x for x in dir(timesfm) if not x.startswith('_')])"
```
Expected: prints the version and exported names (e.g. `TimesFm`, `TimesFmHparams`, `TimesFmCheckpoint`). **The TimesFM API has changed across releases** — use the printed surface and the installed package's README to confirm the constructor/`forecast` signature before writing `model.py`. Adapt Step 7 to match what is actually installed.

- [ ] **Step 7: Implement `timesfm_eval/model.py`** (reference for `timesfm==1.2.0` pytorch; adjust to the version verified in Step 6)

```python
"""Thin adapter isolating all version-specific TimesFM API.

Exposes forecaster(context: np.ndarray, horizon: int) -> np.ndarray of length
`horizon` (predicted PRICE LEVELS), matching metrics.rolling_origin_eval.
"""
import numpy as np


class TimesFMForecaster:
    def __init__(self, context_len: int = 512, horizon_len: int = 128):
        import timesfm
        self._tfm = timesfm.TimesFm(
            hparams=timesfm.TimesFmHparams(
                backend="cpu",
                per_core_batch_size=1,
                context_len=context_len,
                horizon_len=horizon_len,
            ),
            checkpoint=timesfm.TimesFmCheckpoint(
                huggingface_repo_id="google/timesfm-1.0-200m-pytorch"),
        )

    def __call__(self, context: np.ndarray, horizon: int) -> np.ndarray:
        point, _ = self._tfm.forecast([np.asarray(context, dtype=float)], freq=[0])
        return np.asarray(point[0])[:horizon]
```

- [ ] **Step 8: Write the runner `timesfm_eval/run.py`**

```python
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


def _evaluate(name, series, forecaster, outdir):
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
            _evaluate("timesfm", series, tfm, outdir),
            _evaluate("random_walk", series, _rw, outdir),
            _evaluate("drift", series, _drift, outdir),
        ])
        res.insert(0, "asset_id", asset_id)
        all_rows.append(res)
        lines.append(f"\n## {asset_id}\n")
        for h in HORIZONS:
            tf = res[(res.model == "timesfm") & (res.h == h)].iloc[0]
            rw = res[(res.model == "random_walk") & (res.h == h)].iloc[0]
            ss = metrics.skill_score(tf["rmse"], rw["rmse"])
            lines.append(f"- h={h}: TimesFM hit={tf['hit_rate']:.2%}, "
                         f"RMSE skill vs RW={ss:+.3f} "
                         f"({'beats' if ss > 0 else 'loses to'} random walk)")
    pd.concat(all_rows).to_csv(outdir / "skill_summary.csv", index=False)
    lines.append("\n## Verdict\n\nTimesFM is worth referencing only if directional "
                 "hit rate is meaningfully >50% AND RMSE skill vs random walk is "
                 ">0 across horizons and both indices. If hit rate hovers at 50% "
                 "and skill <=0, it adds no usable signal at the index level.\n")
    (outdir / "FINDINGS.md").write_text("\n".join(lines))
    print(f"[timesfm] wrote {outdir}")


if __name__ == "__main__":
    run()
```

- [ ] **Step 9: Smoke-run the experiment** (slow — TimesFM CPU inference over many origins)

Run (in the `.venv-timesfm` venv): `python -m experiments.market_signals.timesfm_eval.run`
Expected: prints `[timesfm] wrote .../results/timesfm_eval`; dir has `skill_summary.csv` and `FINDINGS.md` with hit-rate and skill-vs-RW numbers for each horizon and index. If runtime is excessive, raise `STEP` to reduce the number of origins.

- [ ] **Step 10: Commit**

```bash
git add experiments/market_signals/timesfm_eval/model.py experiments/market_signals/timesfm_eval/run.py
git commit -m "✨ feat: TimesFM index forecast-skill experiment vs naive baselines"
```

---

### Task 6: Top-level README

**Files:**
- Create: `experiments/market_signals/README.md`

- [ ] **Step 1: Write the README**

```markdown
# Market Signal Experiments

Three throwaway exploratory experiments (see
`docs/superpowers/specs/2026-06-30-market-signal-experiments-design.md`).

## Setup

```bash
python -m venv .venv-timesfm && source .venv-timesfm/bin/activate
pip install -r experiments/market_signals/requirements.txt
```

## Run (from repo root)

```bash
python -m experiments.market_signals.fourier.run        # #2 spectral cycles
python -m experiments.market_signals.event_impact.run   # #3 event impulse response
python -m experiments.market_signals.timesfm_eval.run   # #1 TimesFM (slow)
```

Each writes CSV/PNG + a `FINDINGS.md` under `results/<experiment>/` (gitignored).

## What each answers
- **timesfm_eval** — does TimesFM beat random-walk/drift on S&P & NASDAQ returns?
- **fourier** — are there real recurring cycles, or just red noise? (with/without detrend)
- **event_impact** — magnitude + recovery time of war/pandemic/tariff/oil shocks.
```

- [ ] **Step 2: Commit**

```bash
git add experiments/market_signals/README.md
git commit -m "📝 docs: add market_signals experiments README"
```

---

## Self-Review Notes

- **Spec coverage:** shared loader (Task 1) ✓; detrend incl. identity on/off (Task 2) ✓; #2 Fourier + AR(1) significance + on/off comparison (Task 3) ✓; #3 CAAR + Jordà LP + magnitude/recovery/half-life + Iran 2026 in war & oil (Task 4) ✓; #1 TimesFM real run + returns/direction vs naive baselines (Task 5) ✓; isolated requirements + gitignored results (Tasks 1, global constraints) ✓.
- **Type consistency:** `forecaster(context, horizon) -> price-level array` is used identically in `metrics.rolling_origin_eval`, `model.TimesFMForecaster`, and the baselines in `run.py`. `caar_curve` returns columns `[h, caar, se, lo, hi]` consumed by `recovery_horizon`/`half_life`/the plot. `TRANSFORMS` keys `{identity, demean_logret, detrend_linear}` are produced in Task 2 and looped in Task 3.
- **Known external risk:** TimesFM API churns across versions — Task 5 Step 6 verifies the installed surface before `model.py` is written, and all version-specific code is isolated in `model.py`. The model-free harness (`metrics.py`) is fully unit-tested independent of the package.

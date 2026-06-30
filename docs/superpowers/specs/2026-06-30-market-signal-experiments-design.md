# Market Signal Experiments — Design

**Date:** 2026-06-30
**Status:** Approved (brainstorming) — ready for implementation plan
**Type:** Throwaway exploratory research (학습용 탐색)

## Purpose

Three loosely-related exploratory experiments to learn whether certain
quantitative techniques produce signal worth referencing for Croesus. The goal
is **a conclusion, not a production module**: each experiment runs as a
standalone script, dumps CSV + PNG artifacts, and writes up findings in a
markdown report. No integration into the main `croesus/` package at this stage.

The three questions:

1. **TimesFM** — Is Google's TimesFM time-series foundation model good enough on
   market indices (S&P 500, NASDAQ) to be worth referencing as a signal?
2. **Fourier** — Do market indices contain genuine recurring cycles, or are
   apparent cycles spurious?
3. **Event impact** — For macro shocks (war, pandemic, tariff war, oil shock),
   how large is the impact (**magnitude**) and how long does it last
   (**period / recovery time**) on the index?

## Key methodological decision (recorded up front)

The original idea for experiment #3 was to use a Fourier transform to "filter
out other events and isolate a single event's frequency." This is the **wrong
tool**: one-off macro shocks (war, pandemic, tariff, oil) are
**impulses/steps**, not **periodic** signals, so they have no "frequency" to
isolate via FFT. The right tool for "magnitude + period of a shock" is
**event-study / impulse-response (local projection)**, which is exactly what
`experiments/events_impact/` already does. Experiment #3 therefore uses
event-study methods, not Fourier. Experiment #2 keeps Fourier because "are there
recurring cycles?" is a legitimately spectral question (with the expectation
that most apparent cycles will turn out spurious — and *showing* that is itself
the result).

## Scope & shared inputs

- **Indices:** S&P 500 (`^GSPC`) and NASDAQ Composite (`^IXIC`), daily close.
- **History:** maximum available from yfinance (NASDAQ ~1971, S&P ~1927).
- **Data loading:** reuse `experiments/events_impact/data/prices.py`
  (yfinance → DuckDB read-through cache) so all three experiments share one
  cache and one loader.
- **Heavy deps isolated:** `timesfm` + `torch` go in an experiment-local
  `requirements.txt`, **not** the main `pyproject.toml`.

## Structure

```
experiments/market_signals/
├── README.md               # how to run, what each script produces
├── requirements.txt        # timesfm, torch, scipy (isolated heavy deps)
├── common/
│   ├── data.py             # thin wrapper over events_impact prices loader (S&P, NASDAQ)
│   └── detrend.py          # SHARED preprocessing: log returns, drift removal, trend fit
├── timesfm_eval/
│   └── run.py              # experiment #1
├── fourier/
│   └── run.py              # experiment #2
├── event_impact/
│   ├── run.py              # experiment #3 (wraps events_impact event_study)
│   └── events/             # curated event CSVs (war, pandemic, tariff, oil)
└── results/                # CSV + PNG outputs (.gitignored)
```

Each `run.py` is independently runnable (`python -m
experiments.market_signals.<exp>.run`) and writes a `FINDINGS.md` alongside its
artifacts.

## Shared preprocessing — `common/detrend.py`

Per user request, detrending is a **first-class, shared** step (useful beyond
#2). Provides selectable transforms applied before analysis:

- `log_returns(price)` — `diff(log(price))`. Removes the compounding trend;
  default input for spectral analysis.
- `demean_drift(series)` — subtract the mean (average growth rate / drift) so
  the series is centered on zero. This is the user's "평균 시장 성장률을 빼고
  계산" idea, applied to log returns.
- `detrend_logprice(price, kind="linear"|"exp")` — fit and subtract a
  linear/exponential trend from log price, leaving cyclical residual (alternative
  to log-returns when we want to preserve low-frequency structure).

Each experiment declares which transform it uses; the default for #2 is
`demean_drift(log_returns(price))`.

## Experiment #1 — TimesFM evaluation

**Run:** locally for real. Install `timesfm` + `torch` into the experiment venv,
download weights from HuggingFace, run inference on CPU (Apple Silicon). Inference
may take minutes to tens of minutes — acceptable.

**Avoiding the level-forecast trap:** forecasting the price *level* looks
deceptively good because price is persistent. We evaluate **returns / direction**
and compare against **naive baselines** so real information value is visible.

- **Forecast protocol:** rolling-origin out-of-sample. At each origin, feed
  TimesFM the context window, forecast horizons **1, 5, 21** trading days.
- **Baselines:** random walk (last value), drift (mean-return extrapolation).
- **Metrics:**
  - Return RMSE / MAE vs baselines.
  - **Directional hit rate** (did it call up/down correctly?).
  - Skill score relative to naive (does TimesFM beat random walk?).
  - A simple long/flat toy simulation driven by the sign of the forecast,
    reported with the caveat that it ignores costs.
- **Conclusion to produce:** does TimesFM beat naive baselines on direction, and
  is it worth keeping as a reference indicator? Honest "no" is a valid result.

## Experiment #2 — Fourier / spectral

- **Input:** `demean_drift(log_returns(price))` (drift removed, per user). Also
  run on `detrend_logprice` residual as a cross-check.
- **Spectrum:** FFT power spectrum; identify peak frequencies / periods.
- **Significance (the crux):** test whether peaks are real against a null —
  AR(1) red-noise and phase-shuffle/returns-shuffle surrogates. Report which
  peaks (if any) exceed the null confidence band.
- **Time-variation:** spectrogram (STFT) and/or wavelet to see whether any
  periodicity is stable or drifts over time.
- **Sanity checks:** known calendar effects (weekly ~5d, annual ~252d) should
  show up if the pipeline is correct.
- **Conclusion to produce:** are there statistically robust recurring cycles, or
  is the spectrum consistent with red noise? (Expectation: mostly the latter —
  and demonstrating that cleanly is the deliverable.)

## Experiment #3 — Event impact (magnitude + period)

Reuses `experiments/events_impact/analysis/event_study.py` for AR/CAR, extended
with **local projection** to trace the impulse response over horizons so we get
both magnitude and duration.

- **Magnitude:** peak cumulative abnormal return (max drawdown around the event).
- **Period:** trading days until cumulative abnormal return recovers toward zero
  (recovery time).
- **Event categories** (curated CSVs, `events_impact` schema:
  `date,category,magnitude,scope,metadata`):
  - **war:** 9/11 (2001-09-11), Gulf War (1991-01), Russia-Ukraine
    (2022-02-24), **Iran war 2026-03**.
  - **pandemic:** COVID crash (2020-02/03).
  - **tariff:** US-China 2018–2019 rounds, 2025 tariff actions.
  - **oil:** Gulf War oil spike (1990-08), 2008 oil spike, 2022 oil spike,
    **2026-03 Iran oil shock**.
  - (The 2026-03 Iran event appears in **both** war and oil categories with
    appropriate `scope`/`metadata`, since it is simultaneously a conflict shock
    and an oil shock.)
- **Output:** per-category impulse-response curve (CAAR vs horizon with CI band)
  plus a comparison table of magnitude and recovery time across categories.
- **Conclusion to produce:** which shock types hit hardest and which linger
  longest; is impact magnitude/duration consistent enough within a category to
  generalize.

## Out of scope

- Integration into the main `croesus/` pipeline (separate future decision).
- Intraday data (daily only).
- Individual-stock event studies (index-level only, per user).
- Trading-cost-accurate backtests (toy sim only, clearly caveated).

## Success criteria

Each experiment yields a `FINDINGS.md` that answers its question with evidence
(metrics/plots) and an explicit honest verdict — including "this technique does
not add value here" where that is what the data shows.

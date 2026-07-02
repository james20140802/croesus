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

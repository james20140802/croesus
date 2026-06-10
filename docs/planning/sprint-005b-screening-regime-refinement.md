# Sprint 005b: Regime-Aware Screening Refinement

## Goal

Refine the implemented screening engine (Sprint 005) and macro adapter
(Sprint 002) so that MacroState adjusts screening at a finer granularity than
four top-level factor weights, without introducing any learned or
non-deterministic parameters.

```text
MacroState (regime + continuous scores)
  -> momentum horizon weights
  -> continuously interpolated factor weights and filters
  -> volatility-scaled momentum
  -> posture-dependent trend gate
  -> screening_results
```

This is an additive retrofit over completed sprints. It does not change the
proposal engine (Sprint 006), action types, or reason codes. It changes how
candidate scores and eligibility are computed.

Background and rationale live in
`docs/architecture/portfolio-risk-management-operating-model.md`
("계획된 개선: regime-aware screening 세부 조정").

## Motivation

Two known limitations of the current screening structure:

1. `momentum_score` is a simple average of `momentum_1m`, `momentum_3m`, and
   `momentum_6m` percentiles. One-month returns behave as a short-term
   reversal signal rather than momentum, and the 3m/6m horizons are highly
   correlated, so equal averaging adds little diversification.
2. `regime_overrides` in `croesus/macro/config.yaml` apply discrete additive
   deltas to four top-level weights. Weights jump at regime boundaries
   (cliff effect), and the amplifier stress filters switch on abruptly at a
   single threshold.

## Scope

### 1. Regime-specific momentum horizon weights

- Add `momentum_horizon_weights` to `screening.base_weights` config and allow
  per-regime overrides:

```yaml
screening:
  base_momentum_horizon_weights:
    momentum_1m: 0.2
    momentum_3m: 0.3
    momentum_6m: 0.5
  regime_overrides:
    Stagflation:
      momentum: -0.15
      volatility_penalty: 0.15
      momentum_horizon_weights:
        momentum_1m: 0.0
        momentum_3m: 0.3
        momentum_6m: 0.7
```

- `get_screening_params()` and `neutral_screening_params()` return
  `momentum_horizon_weights` alongside `factor_weights`.
- `run_screening` computes `momentum_score` as the weighted average of
  available horizon percentiles instead of `_average(momentum_parts)`.
- Null handling: if a horizon percentile is null, renormalize the remaining
  horizon weights so the score stays comparable across assets. If all
  horizons are null, `momentum_score` stays null (current behavior).

### 2. Continuous weight interpolation

- Replace the discrete regime-delta lookup with deterministic linear
  interpolation between base weights and regime-adjusted weights using the
  continuous MacroState scores:

```text
t = clamp(stress_score / 100, 0, 1)
weight_k = base_weight_k + t * regime_delta_k
```

- Apply the same interpolation to the amplifier stress filters so
  `min_liquidity_multiplier`, `max_volatility_multiplier`, and
  `min_market_cap_multiplier` tighten gradually instead of switching on at
  `amplifier_stress_threshold`.
- Keep the existing discrete behavior available behind a config flag
  (`interpolation: discrete | continuous`) so the change is comparable and
  reversible.
- Round interpolated weights to four decimals (current convention) so results
  remain reproducible.

### 3. Volatility-scaled momentum

- Add volatility-scaled momentum variants as deterministic factor
  transformations:

```text
scaled_momentum_k = momentum_k / volatility_3m
```

- Scaling happens at screening time from stored factor values; no new
  `factor_values` rows and no change to stored factor names in Level 1.
- Percentile ranking then applies to the scaled values when
  `screening.momentum_scaling: vol_scaled` is configured; `raw` preserves
  current behavior and remains the default until backtested.
- Guard: if `volatility_3m` is null or zero, fall back to the raw momentum
  value for that asset and record it in the candidate metadata.

### 4. Posture-dependent trend gate

- When MacroState positioning is `Cautious` or `Defensive`, promote
  `above_200d_ma` from a weighted score component to an eligibility filter:

```text
positioning in {Cautious, Defensive} and above_200d_ma == 0.0
=> skipped: below 200d MA under defensive posture
```

- Config: `screening.trend_gate_postures: [Cautious, Defensive]` (empty list
  disables the gate).
- When the gate is active, exclude `trend_score` from the weighted sum and
  renormalize the remaining weights, so the factor is not double-counted as
  both gate and score.
- Skipped assets keep the existing `skipped` decision bucket with a
  deterministic reason string.

## Non-Goals and Principles

- No learned weights. Regime-conditional factor efficacy estimated from
  historical data is out of scope until a backtest research workflow exists.
  Regimes have very few historical transitions; fitting weights to them is an
  overfitting risk.
- Every adjustment rule must be expressible in `config.yaml` with an economic
  rationale in the architecture doc.
- All outputs remain deterministic given the same inputs and config.
- MacroState still cannot bypass investor profile constraints.

## Schema

No schema changes. `screening_results.factor_scores` and `metadata` JSON
columns already hold per-candidate score detail; extend their payloads with:

```python
{
    "momentum_horizon_weights": {"momentum_1m": 0.0, ...},
    "momentum_scaling": "raw" | "vol_scaled",
    "trend_gate_active": True,
}
```

## Tests

Extend `tests/test_screening.py` and macro adapter tests:

1. Horizon weights from config change `momentum_score` deterministically.
2. Null horizon percentiles renormalize remaining horizon weights.
3. Regime override horizon weights take precedence over base horizon weights.
4. Continuous interpolation at `t = 0` equals base weights and at `t = 1`
   equals the discrete override result.
5. Stress filters tighten monotonically as stress score rises.
6. `interpolation: discrete` reproduces current published results.
7. Vol-scaled momentum changes ranking when volatility differs and falls back
   to raw momentum when `volatility_3m` is missing.
8. Trend gate skips assets below the 200d MA only in configured postures.
9. With the trend gate active, remaining weights renormalize and no asset is
   double-penalized.

## Suggested Task Breakdown

### Task 1: Momentum horizon weights

- Modify: `croesus/macro/config.yaml`, `croesus/macro/screening_adapter.py`,
  `croesus/screening/run_screening.py`
- Commit: `✨ feat: add regime-specific momentum horizon weights`

### Task 2: Continuous interpolation

- Modify: `croesus/macro/config.yaml`, `croesus/macro/screening_adapter.py`
- Commit: `✨ feat: interpolate screening weights from continuous macro scores`

### Task 3: Volatility-scaled momentum

- Modify: `croesus/screening/run_screening.py`, `croesus/macro/config.yaml`
- Commit: `✨ feat: add volatility-scaled momentum option`

### Task 4: Posture-dependent trend gate

- Modify: `croesus/screening/run_screening.py`, `croesus/macro/config.yaml`
- Commit: `✨ feat: gate candidates below 200d MA in defensive postures`

## Acceptance Criteria

- Momentum horizon weights are configurable per regime and default to current
  equal-average behavior when unset.
- With `interpolation: discrete`, screening results are identical to the
  pre-005b implementation.
- With `interpolation: continuous`, weights and filters change smoothly with
  stress score and match the discrete override at full stress.
- Vol-scaled momentum is opt-in via config and falls back safely on missing
  volatility.
- In `Cautious`/`Defensive` postures, assets below the 200d MA are skipped
  with a deterministic reason; in other postures behavior is unchanged.
- All adjustments are visible in persisted `screening_results` metadata so
  reports can explain why a candidate's score or eligibility changed.
- No LLM involvement; no profile constraint is bypassed.

## Out of Scope

- Replacing percentile normalization with z-scores.
- Moving liquidity from score component to a permanent gate (separate
  decision; tracked in the architecture doc discussion).
- 12-1 momentum or short-term reversal as new stored factors.
- Backtesting infrastructure and any data-driven weight estimation.
- Changes to rebalancing logic, action types, or reason codes.

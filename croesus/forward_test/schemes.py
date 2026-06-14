"""
Candidate weight schemes for the forward-test harness.

Valuation cannot be backtested honestly (yfinance gives only the latest
statements, so historical valuation scores would be look-ahead). These schemes
are therefore *forward*-tested: recorded as dated cohorts and measured on
realized, out-of-sample returns.

Each scheme is a ``factor_weights`` dict consumed by ``run_screening`` exactly
like the live macro-adjusted weights. ``volatility_penalty`` is subtracted; the
rest are added; missing dimensions renormalize away (run_screening's contract).
"""
from __future__ import annotations

# Number of names per cohort (redundancy-group-capped at construction time).
COHORT_TOP_N = 10

FORWARD_TEST_SCHEMES: dict[str, dict[str, float]] = {
    # Baseline: today's live macro base weights (valuation already at 0.10).
    # Tracked so composite_v2's value tilt is measured against the status quo,
    # not against an abstract benchmark only.
    "composite_live": {
        "momentum": 0.35,
        "liquidity": 0.25,
        "trend": 0.15,
        "valuation": 0.10,
        "volatility_penalty": 0.15,
    },
    # composite_v2: a real value tilt. Valuation 0.10 -> 0.30, funded out of
    # momentum and liquidity. Value is negatively correlated with momentum, so
    # the multi-factor blend should lower risk; the open question this harness
    # answers with live data is whether it also lifts return.
    "composite_v2_value": {
        "momentum": 0.30,
        "liquidity": 0.10,
        "trend": 0.15,
        "valuation": 0.30,
        "volatility_penalty": 0.15,
    },
    # Aggressive sleeve for a risk-tolerant user. momentum_only posted the
    # highest raw backtest return (also the deepest drawdown); this concentrated
    # momentum scheme is tracked so its REALIZED drawdown is observed live
    # before any capital follows it — opt-in, never a default.
    "momentum_aggressive": {
        "momentum": 0.85,
        "trend": 0.15,
    },
}

BENCHMARK_SYMBOL = "SPY"

"""
Screening factor/dimension taxonomy (Sprint 008b).

Single source of truth for which ``factor_values`` rows the Screening Engine
loads and how each contributes to a score dimension. ADR 0005 assumed new
factors in the long-format table would integrate "automatically" — that was
wrong: a factor is invisible to screening until it is registered here and given
a dimension. This module makes that registration explicit.

Direction matters: valuation is the opposite of momentum — **low** multiples
mean cheap, so their universe percentiles are inverted (``1 - pct``) before
averaging into ``valuation_score``. ``fcf_yield`` is already higher-is-better.

The ``*_vs_sector_pct`` factors are loaded and exposed raw (report / Research
Agent context) but deliberately excluded from the score: they re-express the
same multiples sector-relative, and averaging both would double-count P/E.
"""
from __future__ import annotations

# Price-derived factors (Sprint 001/005) — the pre-008b FACTOR_NAMES.
PRICE_FACTOR_NAMES = (
    "momentum_1m",
    "momentum_3m",
    "momentum_6m",
    "liquidity_1m",
    "above_200d_ma",
    "volatility_3m",
)

# Valuation factors (Sprint 007). All eight are loaded and persisted in
# factor_scores; only the five below feed valuation_score.
VALUATION_FACTOR_NAMES = (
    "pe_ratio",
    "pb_ratio",
    "ev_to_ebitda",
    "fcf_yield",
    "pe_vs_sector_pct",
    "pb_vs_sector_pct",
    "ev_ebitda_vs_sector_pct",
    "price_to_intrinsic",
)

FACTOR_NAMES = PRICE_FACTOR_NAMES + VALUATION_FACTOR_NAMES

# Scored valuation inputs: lower raw value = cheaper = better → invert pct.
VALUATION_INVERTED = (
    "pe_ratio",
    "pb_ratio",
    "ev_to_ebitda",
    "price_to_intrinsic",
)
# Scored valuation inputs that are already higher-is-better.
VALUATION_NATURAL = ("fcf_yield",)

# Raw factor values copied into factor_scores for reports / the Research Agent.
VALUATION_CONTEXT_FACTORS = (
    "pe_ratio",
    "pb_ratio",
    "ev_to_ebitda",
    "fcf_yield",
    "price_to_intrinsic",
    "pe_vs_sector_pct",
    "pb_vs_sector_pct",
    "ev_ebitda_vs_sector_pct",
)

# The dimension sub-scores that gate eligibility in _score_asset. valuation is
# deliberately absent: an asset without fundamentals must not be skipped — its
# valuation weight renormalizes away instead.
SCORE_GROUP_KEYS = (
    "momentum_score",
    "liquidity_score",
    "trend_score",
    "volatility_penalty",
)

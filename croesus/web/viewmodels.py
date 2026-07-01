from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Badge:
    label: str
    value: str
    tone: str = "neutral"  # ok | warn | bad | neutral


@dataclass(frozen=True)
class MacroView:
    date: date | None
    regime: str
    positioning: str
    regime_confidence: float
    amplifier_score: float
    confirmation_score: float
    growth_direction: str = ""    # Expanding | Contracting
    inflation_direction: str = ""  # Rising | Falling
    warnings: list[dict] = field(default_factory=list)
    opportunities: list[dict] = field(default_factory=list)
    regime_methods: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    raw_indicators: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ScreeningRow:
    rank: int | None
    symbol: str
    name: str | None
    score: float | None
    decision_bucket: str
    reason: str
    factor_scores: dict
    asset_id: str | None = None


@dataclass(frozen=True)
class ScreeningView:
    run_id: str | None
    as_of_date: date | None
    rows: list[ScreeningRow] = field(default_factory=list)


@dataclass(frozen=True)
class PortfolioView:
    as_of_date: date | None
    total_market_value: float | None
    unrealized_pnl: float | None
    cost_basis: float | None = None
    return_pct: float | None = None
    base_currency: str = "USD"
    holdings: list[dict] = field(default_factory=list)
    exposures: list[dict] = field(default_factory=list)
    drifts: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)  # [{date, market_value, cost_basis, return_pct}]


@dataclass(frozen=True)
class OpportunityRow:
    asset_id: str
    symbol: str
    name: str | None
    current_price: float | None
    base_upside_pct: float | None
    bands: dict
    grades: dict
    confidence: str | None
    gate_status: str | None = None          # 'pass' | 'warn' | 'block' (Phase E)
    gate_reason_codes: list = field(default_factory=list)
    gate_notes: list = field(default_factory=list)
    # Methodology C (normalized reverse-DCF) — None for the moat-adjusted methodology.
    methodology_key: str | None = None
    plausibility_gap: float | None = None
    implied_growth: float | None = None
    reference_growth: float | None = None
    normalized_upside_pct: float | None = None
    valuation_quality: str | None = None


@dataclass(frozen=True)
class OpportunityView:
    as_of_date: date | None
    rows: list[OpportunityRow] = field(default_factory=list)
    gate_summary: dict | None = None        # {'pass': N, 'warn': N, 'block': N} (Phase E)


@dataclass(frozen=True)
class AssetDetailView:
    asset_id: str
    symbol: str
    name: str | None
    current_price: float | None
    price_history: list[dict] = field(default_factory=list)   # [{date, close}]
    screening: dict | None = None    # {score, rank, decision_bucket, reason, reason_codes, factor_scores}
    raw_factors: dict = field(default_factory=dict)  # {factor_name: value} 최신 factor_values
    thesis: dict | None = None       # thesis_grades row (LLM) or None


@dataclass(frozen=True)
class HomeView:
    macro: Badge | None
    actions: list[dict]
    action_count: int
    opportunity_count: int
    drift_alerts: list[str]
    screening_count: int
    freshness: list[Badge] = field(default_factory=list)
    macro_detail: "MacroView | None" = None
    portfolio: dict | None = None  # {value, pnl, as_of, top_holdings:[{symbol,weight}]}

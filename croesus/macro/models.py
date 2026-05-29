from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class MacroState:
    date: date

    # Layer 1
    regime: str              # Goldilocks | Reflation | Stagflation | Deflation
    regime_confidence: float
    growth_direction: str    # Expanding | Contracting
    inflation_direction: str # Rising | Falling

    # Layer 2
    amplifier_score: float   # 0–100 (higher = more stress)

    # Layer 3
    confirmation_score: float  # -1.0 to +1.0

    # Derived
    positioning: str         # Aggressive | Moderately Aggressive | Neutral | Cautious | Defensive

    # Rule-based alerts
    warnings: list[dict] = field(default_factory=list)
    opportunities: list[dict] = field(default_factory=list)

    # Last values of each indicator series + amplifier category sub-scores
    raw_indicators: dict = field(default_factory=dict)

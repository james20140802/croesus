"""
Data-quality issue model (Sprint 008a).

A ``DataQualityIssue`` is the persistent, structured form of what used to be a
transient warning string: a missing price, a missing FX rate, a quantity that
had to be guessed. ERROR-level issues mean a reported value is misstated, so
downstream reports must surface them instead of presenting the number as clean.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"

# Stable issue codes (part of the product contract, like reason codes).
CODE_PRICE_MISSING = "PRICE_MISSING"
CODE_FX_MISSING = "FX_MISSING"
CODE_QUANTITY_MISSING = "QUANTITY_MISSING"


@dataclass(frozen=True)
class DataQualityIssue:
    domain: str
    severity: str
    code: str
    message: str
    asset_id: str | None = None
    currency: str | None = None
    as_of_date: date | None = None

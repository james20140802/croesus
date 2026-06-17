from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Event types (extend freely; new detectors add new values, no schema change).
EVENT_ABNORMAL_VOLUME = "abnormal_volume"
EVENT_ABNORMAL_RETURN = "abnormal_return"
EVENT_RECENT_DISCLOSURE = "recent_disclosure"
EVENT_VALUATION_DISLOCATION = "valuation_dislocation"

# Directions.
DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
DIRECTION_NEUTRAL = "neutral"

# Source tables (provenance).
SOURCE_PRICES = "prices_daily"
SOURCE_VALUATION = "valuation_snapshots"
SOURCE_DISCLOSURES = "disclosures"


@dataclass(frozen=True)
class Event:
    """A deterministic candidate signal for one asset on one date."""

    asset_id: str
    as_of_date: date
    event_type: str
    direction: str
    magnitude: float
    detail: str
    source: str


@dataclass(frozen=True)
class EventScanResult:
    scanned: list[str] = field(default_factory=list)         # symbols scanned
    events: list[Event] = field(default_factory=list)        # all events emitted
    failed: dict[str, str] = field(default_factory=dict)     # symbol -> error

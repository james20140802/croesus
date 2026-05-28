from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Asset:
    asset_id: str
    symbol: str
    name: str | None
    asset_type: str
    country: str | None = None
    exchange: str | None = None
    currency: str | None = None
    sector: str | None = None
    industry: str | None = None
    is_active: bool = True
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

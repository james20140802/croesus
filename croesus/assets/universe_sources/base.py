"""
Universe source contract (Sprint 008c).

A ``UniverseSource`` lists the constituents of one screening universe (an
index, an exchange segment, a curated list). Sources only *describe* members;
``croesus.assets.ingest_universe`` owns registry writes, so adding a new
universe (KRX, Russell, a thematic list) means implementing this Protocol and
nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UniverseConstituent:
    """One index member as reported by the source, before normalization."""

    symbol: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    index_name: str = ""


class UniverseSource(Protocol):
    source_name: str

    def fetch_constituents(self) -> list[UniverseConstituent]:
        """Return current constituents. Raises on fetch/parse failure —
        degrade policy (skip vs abort) belongs to the caller."""
        ...

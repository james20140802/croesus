from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

SOURCE_FINNHUB = "finnhub"
SOURCE_GDELT = "gdelt"

# Article <-> asset relation kinds.
RELATION_QUERIED = "queried"   # article returned by querying this ticker
RELATION_RELATED = "related"   # listed in the source's related-tickers field
RELATION_ENTITY = "entity"     # extracted by entity recognition (News-2/GDELT)


def make_item_id(source: str, external_id: str) -> str:
    """Deterministic, source-namespaced article id (sha1 hex)."""
    return hashlib.sha1(f"{source}:{external_id}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RawNewsArticle:
    """A news article as parsed from a source, with the tickers it relates to."""

    external_id: str
    url: str | None
    headline: str | None
    summary: str | None
    published_at: datetime | None
    source_name: str | None
    category: str | None
    tickers: tuple[str, ...]   # symbols the source associates (1st = queried)
    body: str | None = None    # full article text (GDELT); None for headline-only sources


@dataclass(frozen=True)
class NewsItem:
    """A persisted article row (without its asset links)."""

    item_id: str
    source: str
    external_id: str
    url: str | None
    headline: str | None
    summary: str | None
    body: str | None
    published_at: datetime | None
    source_name: str | None
    category: str | None


# Not frozen: ``stored`` is an int counter incremented in the ingest loop
# (the frozen sibling results only ever mutate containers; an int needs
# reassignment, so a plain dataclass is the honest choice here).
@dataclass
class NewsIngestionResult:
    scanned: list[str] = field(default_factory=list)      # symbols queried
    stored: int = 0                                        # article rows written
    failed: dict[str, str] = field(default_factory=dict)  # symbol -> error

from __future__ import annotations

from dataclasses import dataclass, field

from croesus.disclosures.models import DEFAULT_SOURCE  # "sec_edgar" (shared)

# Filing-text status values.
STATUS_FETCHED = "fetched"   # non-empty text extracted and stored
STATUS_EMPTY = "empty"       # fetched but no extractable text
STATUS_FAILED = "failed"     # fetch/extract raised (recorded for audit)


@dataclass(frozen=True)
class DisclosureText:
    """The cleaned body text of one filing, keyed to its disclosure."""

    asset_id: str
    accession_number: str
    source_url: str | None
    char_count: int
    text: str
    status: str
    source: str = DEFAULT_SOURCE


@dataclass(frozen=True)
class DisclosureTextIngestionResult:
    fetched: list[str] = field(default_factory=list)      # accession numbers fetched this run
    skipped: list[str] = field(default_factory=list)      # already terminally resolved (fetched/empty)
    deferred: list[str] = field(default_factory=list)     # un-fetched, past this run's limit_per_asset
    failed: dict[str, str] = field(default_factory=dict)  # accession number -> error

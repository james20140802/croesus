from __future__ import annotations

from dataclasses import dataclass
from datetime import date

DEFAULT_SOURCE = "sec_edgar"


@dataclass(frozen=True)
class RawFiling:
    """A filing as parsed from the source, before it is tied to an asset.

    Mirrors the columns of ``disclosures`` minus ``asset_id``/``source`` so the
    pure parser can produce these without knowing which Croesus asset they
    belong to (the ingest loop attaches that).
    """

    accession_number: str
    form_type: str
    filed_date: date
    report_date: date | None
    primary_doc_url: str | None
    title: str | None


@dataclass(frozen=True)
class Disclosure:
    """A filing tied to a Croesus asset, ready to persist to ``disclosures``."""

    asset_id: str
    accession_number: str
    form_type: str
    filed_date: date
    report_date: date | None
    primary_doc_url: str | None
    title: str | None
    source: str = DEFAULT_SOURCE

    @classmethod
    def from_raw(
        cls, asset_id: str, raw: RawFiling, *, source: str = DEFAULT_SOURCE
    ) -> "Disclosure":
        return cls(
            asset_id=asset_id,
            accession_number=raw.accession_number,
            form_type=raw.form_type,
            filed_date=raw.filed_date,
            report_date=raw.report_date,
            primary_doc_url=raw.primary_doc_url,
            title=raw.title,
            source=source,
        )

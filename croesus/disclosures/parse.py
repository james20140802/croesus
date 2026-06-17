from __future__ import annotations

from datetime import date

from croesus.disclosures.models import RawFiling

# EDGAR submissions "recent" can hold up to ~1000 filings; we only want the most
# recent handful per name for the event funnel, so cap the parse.
DEFAULT_LIMIT = 40

_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"


def build_cik_map(company_tickers_payload: dict) -> dict[str, str]:
    """Map UPPER-case ticker -> zero-padded 10-digit CIK string.

    Input is the decoded ``company_tickers.json`` EDGAR publishes: a dict whose
    values are ``{"cik_str": int, "ticker": str, "title": str}``. Entries
    missing a ticker or CIK are skipped.
    """
    cik_map: dict[str, str] = {}
    for entry in company_tickers_payload.values():
        ticker = (entry.get("ticker") or "").upper()
        cik = entry.get("cik_str")
        # ``not cik`` also rejects a 0 cik_str, which would format to the
        # permanently-invalid "0000000000" and 404 every submissions fetch.
        if not ticker or not cik:
            continue
        try:
            cik_map[ticker] = f"{int(cik):010d}"
        except (ValueError, TypeError):
            continue
    return cik_map


def parse_recent_filings(
    submissions_payload: dict,
    *,
    cik: str,
    forms: set[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[RawFiling]:
    """Parse EDGAR ``submissions`` JSON into ``RawFiling`` records, newest first.

    ``cik`` must be a numeric string (zero-padded to 10 digits, as produced by
    ``build_cik_map``). ``forms`` (e.g. ``{"10-K", "10-Q", "8-K"}``) filters by
    form type; ``None`` keeps every form. Rows missing an accession number or a
    parseable filing date are dropped. Stops after ``limit`` kept rows.
    """
    recent = (submissions_payload.get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []
    filing_dates = recent.get("filingDate") or []
    report_dates = recent.get("reportDate") or []
    form_list = recent.get("form") or []
    documents = recent.get("primaryDocument") or []
    descriptions = recent.get("primaryDocDescription") or []

    out: list[RawFiling] = []
    for i, accession in enumerate(accessions):
        form = form_list[i] if i < len(form_list) else None
        # ``not form`` also drops an empty-string form, which a forms=None
        # caller (e.g. a Phase B2 wide-net scrape) would otherwise ingest.
        if not form:
            continue
        if forms is not None and form not in forms:
            continue
        filed = _parse_date(filing_dates[i] if i < len(filing_dates) else None)
        if not accession or filed is None:
            continue
        report = _parse_date(report_dates[i] if i < len(report_dates) else None)
        document = documents[i] if i < len(documents) else None
        description = descriptions[i] if i < len(descriptions) else None
        out.append(
            RawFiling(
                accession_number=accession,
                form_type=form,
                filed_date=filed,
                report_date=report,
                primary_doc_url=_build_doc_url(cik, accession, document),
                # None (not the form) when EDGAR gives no description, so a
                # consumer can tell a real title from a synthesized fallback.
                title=description or None,
            )
        )
        if len(out) >= limit:
            break
    return out


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _build_doc_url(cik: str, accession: str, document: str | None) -> str | None:
    if not document:
        return None
    # The archive path uses the CIK with leading zeros stripped and the
    # accession number with its dashes removed. ``cik`` is a numeric string in
    # production (from build_cik_map); guard so a misuse yields no URL, not a
    # crash that the ingest loop would mis-record as a per-asset failure.
    try:
        cik_int = int(cik)
    except (ValueError, TypeError):
        return None
    accession_nodashes = accession.replace("-", "")
    return f"{_ARCHIVE_BASE}/{cik_int}/{accession_nodashes}/{document}"

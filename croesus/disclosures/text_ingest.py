from __future__ import annotations

from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.disclosures.repository import DisclosureRepository
from croesus.disclosures.source import DEFAULT_FORMS
from croesus.disclosures.text_extract import extract_filing_text
from croesus.disclosures.text_models import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_FETCHED,
    DisclosureText,
    DisclosureTextIngestionResult,
)
from croesus.disclosures.text_repository import DisclosureTextRepository
from croesus.disclosures.text_source import DisclosureTextSource, EdgarDocumentSource

FILER_ASSET_TYPES = ("equity",)
DEFAULT_LIMIT_PER_ASSET = 3


def ingest_disclosure_texts(
    conn: duckdb.DuckDBPyConnection,
    source: DisclosureTextSource | None = None,
    *,
    asset_ids: list[str] | None = None,
    forms: frozenset[str] | None = DEFAULT_FORMS,
    limit_per_asset: int = DEFAULT_LIMIT_PER_ASSET,
    log: Callable[[str], None] = print,
) -> DisclosureTextIngestionResult:
    """Fetch and store body text for recent filings that lack it.

    For each active equity (optionally restricted to ``asset_ids``), takes the
    most-recent ``limit_per_asset`` filings with a ``primary_doc_url`` and a
    matching form that are not yet terminally resolved (neither fetched nor
    confirmed empty), fetches the document, extracts clean text, and upserts it.
    A failed fetch is recorded as a 'failed' row (retried next run) and isolated
    so one bad document never stops the run. Filings beyond ``limit_per_asset``
    this run are reported in ``deferred`` (a later run picks them up).
    """
    source = source or EdgarDocumentSource()
    wanted = set(asset_ids) if asset_ids is not None else None
    assets = [
        a
        for a in AssetRepository(conn).list_active()
        if a.asset_type in FILER_ASSET_TYPES and (wanted is None or a.asset_id in wanted)
    ]
    disclosures = DisclosureRepository(conn)
    texts = DisclosureTextRepository(conn)
    result = DisclosureTextIngestionResult()

    for asset in assets:
        already = texts.terminal_accessions(asset.asset_id)
        candidates = [
            d
            for d in disclosures.load_for_asset(asset.asset_id)
            if d.primary_doc_url and (forms is None or d.form_type in forms)
        ]
        # Terminally-resolved filings (fetched/empty) are reported skipped.
        result.skipped.extend(
            d.accession_number for d in candidates if d.accession_number in already
        )
        pending = [d for d in candidates if d.accession_number not in already]
        todo = pending[:limit_per_asset]
        # Anything past this run's budget is deferred (a later run picks it up).
        result.deferred.extend(d.accession_number for d in pending[limit_per_asset:])

        for disclosure in todo:
            try:
                html = source.fetch_document(disclosure.primary_doc_url)
                text = extract_filing_text(html)
                status = STATUS_FETCHED if text else STATUS_EMPTY
                texts.upsert([
                    DisclosureText(
                        asset_id=asset.asset_id,
                        accession_number=disclosure.accession_number,
                        source_url=disclosure.primary_doc_url,
                        char_count=len(text),
                        text=text,
                        status=status,
                    )
                ])
                if status == STATUS_FETCHED:
                    result.fetched.append(disclosure.accession_number)
                log(f"{asset.symbol} {disclosure.accession_number}: {status} ({len(text)} chars)")
            except Exception as exc:  # noqa: BLE001 - per-filing failures must not stop the run.
                result.failed[disclosure.accession_number] = str(exc)
                texts.upsert([
                    DisclosureText(
                        asset_id=asset.asset_id,
                        accession_number=disclosure.accession_number,
                        source_url=disclosure.primary_doc_url,
                        char_count=0,
                        text="",
                        status=STATUS_FAILED,
                    )
                ])
                log(f"failed {asset.symbol} {disclosure.accession_number}: {exc}")

    return result

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from croesus.assets.models import Asset
from croesus.factors.equity.repository import (
    ValuationSnapshot,
    ValuationSnapshotRepository,
)
from croesus.fundamentals.repository import (
    METRIC_FREE_CASH_FLOW,
    METRIC_NET_INCOME,
    METRIC_REVENUE,
    FundamentalsRepository,
)
from croesus.news.models import NewsItem
from croesus.news.repository import NewsRepository

DEFAULT_FILING_CHAR_BUDGET = 24_000
DEFAULT_NEWS_LIMIT = 10

# Key fundamentals surfaced to the grader as numeric context.
_FUNDAMENTAL_METRICS = {
    "revenue": METRIC_REVENUE,
    "free_cash_flow": METRIC_FREE_CASH_FLOW,
    "net_income": METRIC_NET_INCOME,
}


@dataclass(frozen=True)
class ThesisEvidence:
    filing_excerpt: str | None
    filing_form: str | None
    filing_date: date | None
    news: list[NewsItem]
    valuation: ValuationSnapshot | None
    fundamentals: dict[str, float | None]


def assemble_thesis_evidence(
    conn: duckdb.DuckDBPyConnection,
    asset: Asset,
    as_of: date,
    *,
    filing_char_budget: int = DEFAULT_FILING_CHAR_BUDGET,
    news_limit: int = DEFAULT_NEWS_LIMIT,
) -> ThesisEvidence:
    """Bundle filing text + news + numeric context for one asset. Best-effort:
    a missing source yields None / empty, never an error."""
    filing_form, filing_date, filing_excerpt = _load_latest_filing(
        conn, asset.asset_id, filing_char_budget
    )
    news = NewsRepository(conn).load_for_asset(asset.asset_id, limit=news_limit)
    valuation = ValuationSnapshotRepository(conn).get(asset.asset_id, as_of)
    funds = FundamentalsRepository(conn)
    fundamentals = {
        label: funds.get_latest_metric(asset.asset_id, metric)
        for label, metric in _FUNDAMENTAL_METRICS.items()
    }
    return ThesisEvidence(
        filing_excerpt=filing_excerpt,
        filing_form=filing_form,
        filing_date=filing_date,
        news=news,
        valuation=valuation,
        fundamentals=fundamentals,
    )


def _load_latest_filing(
    conn: duckdb.DuckDBPyConnection, asset_id: str, char_budget: int
) -> tuple[str | None, date | None, str | None]:
    row = conn.execute(
        """
        SELECT d.form_type, d.filed_date, t.text
        FROM disclosure_texts t
        JOIN disclosures d
          ON d.asset_id = t.asset_id AND d.accession_number = t.accession_number
        WHERE t.asset_id = ? AND t.status = 'fetched' AND length(t.text) > 0
        ORDER BY d.filed_date DESC
        LIMIT 1
        """,
        [asset_id],
    ).fetchone()
    if row is None:
        return None, None, None
    form_type, filed_date, text = row
    excerpt = text[:char_budget] if text else None
    return form_type, filed_date, excerpt

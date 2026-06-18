from __future__ import annotations

from datetime import datetime, timezone

from croesus.news.models import RawNewsArticle


def parse_company_news(payload: list[dict], *, symbol: str) -> list[RawNewsArticle]:
    """Parse Finnhub ``/company-news`` JSON into ``RawNewsArticle`` records.

    The queried ``symbol`` is always the first ticker; Finnhub's ``related``
    field (comma-separated) adds the rest, de-duplicated and upper-cased. Rows
    without a usable article id are dropped.
    """
    queried = symbol.upper()
    out: list[RawNewsArticle] = []
    for row in payload:
        article_id = row.get("id")
        if not article_id:  # 0 / None / missing -> no stable external id
            continue
        out.append(
            RawNewsArticle(
                external_id=str(article_id),
                url=row.get("url") or None,
                headline=row.get("headline") or None,
                summary=row.get("summary") or None,
                published_at=_parse_epoch(row.get("datetime")),
                source_name=row.get("source") or None,
                category=row.get("category") or None,
                tickers=_tickers(queried, row.get("related")),
            )
        )
    return out


def _tickers(queried: str, related: str | None) -> tuple[str, ...]:
    ordered = [queried]
    for raw in (related or "").split(","):
        ticker = raw.strip().upper()
        if ticker and ticker not in ordered:
            ordered.append(ticker)
    return tuple(ordered)


def _parse_epoch(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError, OSError):
        return None

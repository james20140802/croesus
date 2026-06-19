from __future__ import annotations

import re
from datetime import datetime, timezone

from croesus.news.models import RawNewsArticle

# Common corporate-name suffixes to strip so the keyword query matches plain news
# prose ("Apple" not "Apple Inc."). Order-independent; applied as whole words.
_SUFFIXES = (
    "incorporated", "inc", "corporation", "corp", "company", "co",
    "limited", "ltd", "plc", "holdings", "group", "class a", "class b",
)
_SUFFIX_RE = re.compile(
    r"[,\.]?\s*\b(" + "|".join(_SUFFIXES) + r")\b\.?\s*$", re.IGNORECASE
)


def company_query_term(name: str | None) -> str:
    """Clean a company name into a quoted GDELT keyword phrase.

    Strips trailing corporate suffixes ("Inc.", "Corporation", "Class A", …) and
    wraps the result in quotes for an exact-phrase match. Returns "" when there
    is no usable name (caller skips that asset).
    """
    if not name:
        return ""
    cleaned = name.strip()
    # Strip suffixes repeatedly (e.g. "Alphabet Inc. Class A" -> "Alphabet").
    while True:
        stripped = _SUFFIX_RE.sub("", cleaned).strip(" ,.")
        if stripped == cleaned or not stripped:
            break
        cleaned = stripped
    return f'"{cleaned}"' if cleaned else ""


def parse_gdelt_doc(payload: dict) -> list[RawNewsArticle]:
    """Parse a GDELT DOC 2.0 ``artlist`` JSON response into ``RawNewsArticle``.

    Tickers are left empty (the ingest job attaches the queried asset) and body
    is None (fetched separately). Rows without a URL are dropped.
    """
    articles = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(articles, list):
        return []
    out: list[RawNewsArticle] = []
    for row in articles:
        url = row.get("url") or None
        if not url:
            continue
        out.append(
            RawNewsArticle(
                external_id=url,
                url=url,
                headline=row.get("title") or None,
                summary=None,
                published_at=_parse_seendate(row.get("seendate")),
                source_name=row.get("domain") or None,
                category=None,
                tickers=(),
            )
        )
    return out


def _parse_seendate(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None

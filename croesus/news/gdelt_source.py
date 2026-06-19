from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import requests

from croesus.news.gdelt_parse import parse_gdelt_doc
from croesus.news.models import RawNewsArticle

_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_MAX_RECORDS = 25


@runtime_checkable
class GdeltNewsSource(Protocol):
    name: str

    def fetch_articles(
        self, query_term: str, *, since: date, until: date
    ) -> list[RawNewsArticle]:
        """Return articles matching ``query_term`` in ``[since, until]``."""


class GdeltDocSource:
    """GDELT DOC 2.0 API adapter (free, open, no key)."""

    name = "gdelt"

    def __init__(
        self, *, max_records: int = DEFAULT_MAX_RECORDS, timeout: float = 20.0
    ) -> None:
        self._max_records = max_records
        self._timeout = timeout

    def build_params(self, query_term: str, *, since: date, until: date) -> dict:
        return {
            "query": f"{query_term} sourcelang:english",
            "mode": "artlist",
            "format": "json",
            "maxrecords": self._max_records,
            "sort": "DateDesc",
            "startdatetime": since.strftime("%Y%m%d000000"),
            # End-of-day, else GDELT excludes the whole `until` day (its news arrives
            # after 00:00:00); with lookback_days=0 the window would collapse to nothing.
            "enddatetime": until.strftime("%Y%m%d235959"),
        }

    def fetch_articles(
        self, query_term: str, *, since: date, until: date
    ) -> list[RawNewsArticle]:
        resp = requests.get(
            _DOC_URL,
            params=self.build_params(query_term, since=since, until=until),
            timeout=self._timeout,
        )
        resp.raise_for_status()
        # GDELT returns an empty body (not JSON) when a query matches nothing.
        if not resp.text.strip():
            return []
        return parse_gdelt_doc(resp.json())

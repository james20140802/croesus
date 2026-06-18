from __future__ import annotations

import os
from datetime import date
from typing import Protocol, runtime_checkable

import requests

from croesus.news.models import RawNewsArticle
from croesus.news.parse import parse_company_news

_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"


@runtime_checkable
class NewsSource(Protocol):
    name: str

    def fetch_company_news(
        self, symbol: str, *, since: date, until: date
    ) -> list[RawNewsArticle]:
        """Return articles mentioning ``symbol`` published in ``[since, until]``."""


class FinnhubNewsSource:
    """Finnhub ``/company-news`` adapter (free tier; ticker-tagged)."""

    name = "finnhub"

    def __init__(
        self, api_key: str | None = None, *, timeout: float = 15.0
    ) -> None:
        self._api_key = api_key or os.getenv("CROESUS_FINNHUB_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Finnhub API key required: set CROESUS_FINNHUB_API_KEY or pass api_key"
            )
        self._timeout = timeout

    def fetch_company_news(
        self, symbol: str, *, since: date, until: date
    ) -> list[RawNewsArticle]:
        resp = requests.get(
            _COMPANY_NEWS_URL,
            params={
                "symbol": symbol,
                "from": since.isoformat(),
                "to": until.isoformat(),
                "token": self._api_key,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return parse_company_news(resp.json(), symbol=symbol)

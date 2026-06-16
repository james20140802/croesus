from __future__ import annotations

import os
from typing import Protocol

import requests

from croesus.disclosures.models import RawFiling
from croesus.disclosures.parse import build_cik_map, parse_recent_filings

# SEC requires a descriptive User-Agent with contact info; without one EDGAR
# returns 403. Overridable via env for deployment.
DEFAULT_USER_AGENT = "croesus research (drchasekim@gmail.com)"
DEFAULT_FORMS = frozenset({"10-K", "10-Q", "8-K"})

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


class DisclosureSource(Protocol):
    def fetch_recent_filings(self, symbol: str) -> list[RawFiling]:
        """Return recent filings for a ticker, newest first; empty if unknown."""


class EdgarDisclosureSource:
    """Fetches recent filing metadata from SEC EDGAR's public JSON API.

    The ticker->CIK map is fetched once and cached on the instance. All filing
    parsing is delegated to the pure functions in ``parse`` so this class only
    owns the HTTP concerns.
    """

    def __init__(
        self,
        user_agent: str | None = None,
        *,
        forms: frozenset[str] | None = DEFAULT_FORMS,
        limit: int = 40,
        timeout: float = 15.0,
    ) -> None:
        self._user_agent = user_agent or os.getenv(
            "CROESUS_SEC_USER_AGENT", DEFAULT_USER_AGENT
        )
        self._forms = set(forms) if forms is not None else None
        self._limit = limit
        self._timeout = timeout
        self._cik_map: dict[str, str] | None = None

    def fetch_recent_filings(self, symbol: str) -> list[RawFiling]:
        cik_map = self._ensure_cik_map()
        cik = cik_map.get(symbol.upper())
        if cik is None:
            return []
        resp = requests.get(
            _SUBMISSIONS_URL.format(cik=cik),
            headers=self._headers(),
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return parse_recent_filings(
            resp.json(), cik=cik, forms=self._forms, limit=self._limit
        )

    def _ensure_cik_map(self) -> dict[str, str]:
        if self._cik_map is None:
            resp = requests.get(
                _TICKER_MAP_URL, headers=self._headers(), timeout=self._timeout
            )
            resp.raise_for_status()
            self._cik_map = build_cik_map(resp.json())
        return self._cik_map

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"}

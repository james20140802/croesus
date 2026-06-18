from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

import requests

from croesus.disclosures.source import DEFAULT_USER_AGENT


@runtime_checkable
class DisclosureTextSource(Protocol):
    def fetch_document(self, url: str) -> str:
        """Return the raw document (HTML/text) at ``url``."""


class EdgarDocumentSource:
    """Fetches a filing's primary document over HTTP from sec.gov.

    Reuses the SEC ``User-Agent`` discipline from ``EdgarDisclosureSource``
    (EDGAR returns 403 without a descriptive contact UA).
    """

    def __init__(self, user_agent: str | None = None, *, timeout: float = 30.0) -> None:
        self._user_agent = user_agent or os.getenv(
            "CROESUS_SEC_USER_AGENT", DEFAULT_USER_AGENT
        )
        self._timeout = timeout

    def fetch_document(self, url: str) -> str:
        resp = requests.get(url, headers=self._headers(), timeout=self._timeout)
        resp.raise_for_status()
        # EDGAR often serves text/html with no charset, so requests would fall
        # back to ISO-8859-1 and mangle UTF-8 filings. Detect the real encoding
        # when the server didn't declare one.
        if "charset" not in resp.headers.get("Content-Type", "").lower():
            resp.encoding = resp.apparent_encoding or resp.encoding
        return resp.text

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"}

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ArticleBodyFetcher(Protocol):
    def fetch_body(self, url: str) -> str | None:
        """Return the cleaned article body text at ``url``, or None if unavailable."""


class TrafilaturaBodyFetcher:
    """Fetches and extracts an article's main text with ``trafilatura``.

    ``trafilatura`` is imported lazily so tests (which inject a fake fetcher)
    don't require it installed, and an extraction failure yields ``None`` rather
    than raising — a missing body must never stop a news ingest run.
    """

    def __init__(self, *, timeout: float = 20.0) -> None:
        self._timeout = timeout

    def fetch_body(self, url: str) -> str | None:
        import trafilatura

        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded)
        return text or None

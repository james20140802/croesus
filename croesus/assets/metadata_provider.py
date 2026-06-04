from __future__ import annotations

from typing import Protocol

from croesus.assets.models import Asset


class AssetMetadataProvider(Protocol):
    """Provider that resolves a user-facing symbol into normalized asset metadata."""

    def get_asset(self, symbol: str) -> Asset | None:
        """Return a normalized Asset row for ``symbol``, or None if unresolved."""

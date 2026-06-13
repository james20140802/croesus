"""
Economic redundancy grouping for portfolio construction.

Two securities can be *distinct instruments* yet carry near-identical economic
exposure: the share classes of one issuer (Alphabet's GOOG/GOOGL, Fox's
FOX/FOXA) move on the same cash flows; ETFs tracking one index (SPY/VOO/IVV on
the S&P 500) move on the same basket. Holding both does not diversify — it
doubles a single bet.

This module identifies those groups *from registry data* (issuer name, fund
name) without hard-coded ticker lists, so the universe and the screening
ranking keep every instrument while portfolio construction can cap a group's
**combined** weight at a single position's worth.

It never removes anything; a singleton simply maps to its own ``asset_id`` and
gets no special treatment.
"""
from __future__ import annotations

import re

# Corporate-form tokens dropped when normalizing an issuer name, so that
# "Alphabet Inc." and "Alphabet Inc. (Class C)" reduce to the same issuer.
_CORP_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "ltd",
        "limited",
        "plc",
        "holdings",
        "holding",
        "group",
        "sa",
        "nv",
        "ag",
        "the",
        "class",
    }
)

# Index-name fragments → canonical index key. Keyed on the index, not on any
# ticker, so any fund whose name contains the fragment groups automatically.
# Order matters: the first fragment found in a name wins.
_INDEX_TOKENS: tuple[tuple[str, str], ...] = (
    ("s&p 500", "sp500"),
    ("s&p500", "sp500"),
    ("sp 500", "sp500"),
    ("nasdaq 100", "nasdaq100"),
    ("nasdaq-100", "nasdaq100"),
    ("nasdaq100", "nasdaq100"),
    ("total stock market", "total_us_market"),
    ("total market", "total_us_market"),
    ("dow jones industrial", "dow30"),
    ("russell 2000", "russell2000"),
)

_CLASS_SUFFIX = re.compile(r"\(class\s+[a-z]\)", re.IGNORECASE)
_NON_WORD = re.compile(r"[^a-z0-9\s]")

_ETF_TYPES = frozenset({"etf", "bond_etf", "reit_etf", "leveraged_etf"})


def _issuer_key(name: str) -> str:
    """Normalize an equity's issuer name to a stable share-class-agnostic key.

    Strips a ``(Class X)`` suffix, punctuation, and corporate-form tokens, so
    both Alphabet classes collapse to ``alphabet`` while distinct issuers stay
    distinct.
    """
    text = _CLASS_SUFFIX.sub(" ", name.lower())
    text = _NON_WORD.sub(" ", text)
    tokens = [t for t in text.split() if t and t not in _CORP_SUFFIXES]
    return "issuer:" + " ".join(tokens) if tokens else "issuer:" + name.strip().lower()


def _index_key(name: str) -> str | None:
    """Canonical index key for an index ETF, or ``None`` if no index is named."""
    lowered = name.lower()
    for fragment, key in _INDEX_TOKENS:
        if fragment in lowered:
            return "index:" + key
    return None


def redundancy_key(name: str, asset_type: str) -> str | None:
    """Group key for a security, or ``None`` when it has no redundant peers.

    Equities key on normalized issuer (share classes group). Index ETFs key on
    the tracked index parsed from the fund name. Everything else — cash, single
    bonds, thematic ETFs with no recognized index — returns ``None``.
    """
    if asset_type == "equity":
        return _issuer_key(name)
    if asset_type in _ETF_TYPES:
        return _index_key(name)
    return None


def group_keys(items: dict[str, tuple[str, str]]) -> dict[str, str]:
    """Map each ``asset_id`` to its redundancy-group key.

    ``items`` maps ``asset_id -> (name, asset_type)``. A key is only shared
    when **two or more** assets in this batch resolve to it — so grouping
    reflects an actual redundant peer present here, never an incidental name
    normalization. Every other asset maps to its own ``asset_id`` and forms a
    singleton group with no combined-weight cap.
    """
    raw: dict[str, str | None] = {
        asset_id: redundancy_key(name, asset_type)
        for asset_id, (name, asset_type) in items.items()
    }
    shared: dict[str, int] = {}
    for key in raw.values():
        if key is not None:
            shared[key] = shared.get(key, 0) + 1
    return {
        asset_id: key if key is not None and shared[key] > 1 else asset_id
        for asset_id, key in raw.items()
    }

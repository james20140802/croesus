"""Redundancy grouping: share classes of one issuer, ETFs on one index."""
from __future__ import annotations

from croesus.screening.redundancy import group_keys, redundancy_key


def test_share_classes_of_one_issuer_share_a_key() -> None:
    # GOOGL lacks the "(Class A)" suffix GOOG carries, yet both must group:
    # the issuer normalization has to survive the asymmetry.
    goog = redundancy_key("Alphabet Inc. (Class C)", "equity")
    googl = redundancy_key("Alphabet Inc.", "equity")
    assert goog == googl
    assert redundancy_key("Fox Corporation (Class B)", "equity") == redundancy_key(
        "Fox Corporation (Class A)", "equity"
    )


def test_distinct_issuers_do_not_collide() -> None:
    apple = redundancy_key("Apple Inc.", "equity")
    microsoft = redundancy_key("Microsoft Corporation", "equity")
    alphabet = redundancy_key("Alphabet Inc.", "equity")
    assert len({apple, microsoft, alphabet}) == 3


def test_index_etfs_tracking_one_index_share_a_key() -> None:
    spy = redundancy_key("SPDR S&P 500 ETF Trust", "etf")
    voo = redundancy_key("Vanguard S&P 500 ETF", "etf")
    ivv = redundancy_key("iShares Core S&P 500 ETF", "etf")
    assert spy == voo == ivv
    assert spy is not None
    # A Nasdaq-100 fund is a different index, so a different key.
    assert redundancy_key("Invesco NASDAQ 100 ETF", "etf") != spy


def test_etf_without_a_recognised_index_is_unique() -> None:
    # No index token in the name -> not grouped (treated as its own bet).
    assert redundancy_key("ARK Innovation ETF", "etf") is None


def test_group_keys_falls_back_to_asset_id_for_singletons() -> None:
    items = {
        "US_EQ_GOOG": ("Alphabet Inc. (Class C)", "equity"),
        "US_EQ_GOOGL": ("Alphabet Inc.", "equity"),
        "US_EQ_AAPL": ("Apple Inc.", "equity"),
        "US_ETF_SPY": ("SPDR S&P 500 ETF Trust", "etf"),
        "US_ETF_VOO": ("Vanguard S&P 500 ETF", "etf"),
        "US_ETF_ARKK": ("ARK Innovation ETF", "etf"),
    }
    groups = group_keys(items)
    # The two Alphabet classes collapse to one group; the two S&P 500 funds too.
    assert groups["US_EQ_GOOG"] == groups["US_EQ_GOOGL"]
    assert groups["US_ETF_SPY"] == groups["US_ETF_VOO"]
    # Singletons keep their own asset_id as the group key (no cap effect).
    assert groups["US_EQ_AAPL"] == "US_EQ_AAPL"
    assert groups["US_ETF_ARKK"] == "US_ETF_ARKK"
    # Five distinct economic bets, not six tickers.
    assert len(set(groups.values())) == 4

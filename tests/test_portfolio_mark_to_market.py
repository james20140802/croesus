from datetime import date

from croesus.portfolio.mark_to_market import mark_to_market
from croesus.portfolio.models import AssetAttrs, Holding


AS_OF = date(2026, 6, 1)


def _holding(
    asset_id: str,
    *,
    quantity: float = 0.0,
    market_value: float | None = None,
    currency: str = "USD",
    avg_cost: float | None = None,
) -> Holding:
    return Holding(
        "default",
        asset_id,
        AS_OF,
        quantity,
        market_value,
        currency,
        avg_cost=avg_cost,
    )


def test_mark_to_market_uses_latest_close_and_fx_for_market_value_and_pnl() -> None:
    holdings = [
        _holding("US_EQ_AAPL", quantity=10, currency="USD", avg_cost=150.0),
        _holding("KR_EQ_005930", quantity=3, currency="KRW", avg_cost=60_000.0),
        _holding("CASH_KRW", market_value=150_000.0, currency="KRW"),
    ]
    prices = {"US_EQ_AAPL": 190.0, "KR_EQ_005930": 70_000.0}
    assets = {
        "US_EQ_AAPL": AssetAttrs(currency="USD"),
        "KR_EQ_005930": AssetAttrs(currency="KRW"),
    }

    result = mark_to_market(
        holdings,
        price_lookup=lambda asset_id: prices.get(asset_id),
        fx_rates={"USD": 1.0, "KRW": 1500.0},
        assets_by_id=assets,
        base_currency="USD",
        as_of_date=AS_OF,
    )

    by_asset = {h.asset_id: h for h in result.holdings}
    assert by_asset["US_EQ_AAPL"].market_value == 1900.0
    assert by_asset["US_EQ_AAPL"].cost_basis == 1500.0
    assert by_asset["US_EQ_AAPL"].metadata["price_source"] == "store"
    assert by_asset["US_EQ_AAPL"].metadata["unrealized_pnl"] == 400.0
    assert by_asset["US_EQ_AAPL"].metadata["return_pct"] == 400.0 / 1500.0

    assert by_asset["KR_EQ_005930"].market_value == 140.0
    assert by_asset["KR_EQ_005930"].cost_basis == 120.0
    assert by_asset["KR_EQ_005930"].metadata["unrealized_pnl"] == 20.0

    assert by_asset["CASH_KRW"].market_value == 100.0
    assert by_asset["CASH_KRW"].cost_basis == 100.0
    assert by_asset["CASH_KRW"].metadata["price_source"] == "cash"

    assert result.total_market_value == 2140.0
    assert result.total_cost_basis == 1720.0
    assert result.unrealized_pnl == 420.0
    assert result.warnings == []


def test_mark_to_market_keeps_manual_market_value_when_price_missing() -> None:
    holdings = [
        _holding(
            "US_EQ_AAPL",
            quantity=10,
            market_value=1800.0,
            currency="USD",
            avg_cost=150.0,
        )
    ]

    result = mark_to_market(
        holdings,
        price_lookup=lambda asset_id: None,
        fx_rates={"USD": 1.0},
        assets_by_id={"US_EQ_AAPL": AssetAttrs(currency="USD")},
        base_currency="USD",
        as_of_date=AS_OF,
    )

    holding = result.holdings[0]
    assert holding.market_value == 1800.0
    assert holding.cost_basis == 1500.0
    assert holding.metadata["price_source"] == "manual"
    assert any("PRICE_MISSING" in warning and "manual" in warning for warning in result.warnings)


def test_mark_to_market_keeps_legacy_manual_value_when_quantity_is_missing() -> None:
    result = mark_to_market(
        [_holding("US_EQ_AAPL", market_value=1800.0, currency="USD")],
        price_lookup=lambda asset_id: 190.0,
        fx_rates={"USD": 1.0},
        assets_by_id={"US_EQ_AAPL": AssetAttrs(currency="USD")},
        base_currency="USD",
        as_of_date=AS_OF,
    )

    holding = result.holdings[0]
    assert holding.market_value == 1800.0
    assert holding.metadata["price_source"] == "manual"


def test_mark_to_market_falls_back_to_cost_basis_when_price_and_manual_value_missing() -> None:
    result = mark_to_market(
        [_holding("US_EQ_AAPL", quantity=10, currency="USD", avg_cost=150.0)],
        price_lookup=lambda asset_id: None,
        fx_rates={"USD": 1.0},
        assets_by_id={"US_EQ_AAPL": AssetAttrs(currency="USD")},
        base_currency="USD",
        as_of_date=AS_OF,
    )

    holding = result.holdings[0]
    assert holding.market_value == 1500.0
    assert holding.cost_basis == 1500.0
    assert holding.metadata["price_source"] == "cost_basis"
    assert any("PRICE_MISSING" in warning and "cost_basis" in warning for warning in result.warnings)


def test_mark_to_market_warns_and_uses_one_to_one_when_fx_missing() -> None:
    result = mark_to_market(
        [_holding("KR_EQ_005930", quantity=3, currency="KRW", avg_cost=60_000.0)],
        price_lookup=lambda asset_id: 70_000.0,
        fx_rates={"USD": 1.0},
        assets_by_id={"KR_EQ_005930": AssetAttrs(currency="KRW")},
        base_currency="USD",
        as_of_date=AS_OF,
    )

    assert result.holdings[0].market_value == 210_000.0
    assert any("FX_MISSING" in warning and "KRW" in warning for warning in result.warnings)

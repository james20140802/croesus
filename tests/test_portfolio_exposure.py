from datetime import date

from croesus.portfolio.exposure import ExposureLimits, compute_exposures
from croesus.portfolio.models import AssetAttrs, Holding
from croesus.portfolio.policy import compute_policy_drifts
from croesus.profiles.models import PolicyTarget

AS_OF = date(2026, 6, 1)


def _h(asset_id: str, market_value: float, currency: str = "USD") -> Holding:
    return Holding("default", asset_id, AS_OF, 0.0, market_value, currency)


def _exposures(holdings, assets_by_id, limits=None, **kwargs):
    return compute_exposures(
        holdings,
        assets_by_id,
        limits or ExposureLimits(),
        portfolio_id="default",
        as_of_date=AS_OF,
        **kwargs,
    )


def _by_type(exposures, exposure_type):
    return {e.exposure_name: e for e in exposures if e.exposure_type == exposure_type}


# -- position ------------------------------------------------------------------


def test_position_weights_sum_to_one() -> None:
    holdings = [_h("US_EQ_AAPL", 1900), _h("US_EQ_MSFT", 2100), _h("CASH_USD", 1000)]
    attrs = {
        "US_EQ_AAPL": AssetAttrs(asset_type="equity", sector="Technology"),
        "US_EQ_MSFT": AssetAttrs(asset_type="equity", sector="Technology"),
    }

    positions = _by_type(_exposures(holdings, attrs), "position")

    assert set(positions) == {"US_EQ_AAPL", "US_EQ_MSFT", "CASH_USD"}
    assert abs(sum(e.weight for e in positions.values()) - 1.0) < 1e-9


# -- sector --------------------------------------------------------------------


def test_sector_exposure_aggregates_multiple_holdings() -> None:
    holdings = [_h("US_EQ_AAPL", 1900), _h("US_EQ_MSFT", 2100), _h("CASH_USD", 1000)]
    attrs = {
        "US_EQ_AAPL": AssetAttrs(asset_type="equity", sector="Technology"),
        "US_EQ_MSFT": AssetAttrs(asset_type="equity", sector="Technology"),
    }

    sectors = _by_type(_exposures(holdings, attrs), "sector")

    assert abs(sectors["Technology"].weight - 0.8) < 1e-9
    assert abs(sectors["Technology"].market_value - 4000.0) < 1e-9
    assert abs(sectors["Cash"].weight - 0.2) < 1e-9


# -- theme ---------------------------------------------------------------------


def test_theme_exposure_reads_theme_tags() -> None:
    holdings = [_h("US_EQ_AAPL", 2000), _h("US_EQ_MSFT", 2000)]
    attrs = {
        "US_EQ_AAPL": AssetAttrs(asset_type="equity", theme_tags=["ai", "semiconductor"]),
        "US_EQ_MSFT": AssetAttrs(asset_type="equity", theme_tags=["ai"]),
    }

    themes = _by_type(_exposures(holdings, attrs), "theme")

    assert abs(themes["ai"].weight - 1.0) < 1e-9
    assert abs(themes["semiconductor"].weight - 0.5) < 1e-9


def test_theme_exposure_skips_holdings_without_tags() -> None:
    holdings = [_h("US_EQ_AAPL", 2000), _h("US_EQ_MSFT", 2000)]
    attrs = {
        "US_EQ_AAPL": AssetAttrs(asset_type="equity", theme_tags=["ai"]),
        "US_EQ_MSFT": AssetAttrs(asset_type="equity", theme_tags=[]),
    }

    themes = _by_type(_exposures(holdings, attrs), "theme")

    assert set(themes) == {"ai"}


# -- country / currency --------------------------------------------------------


def test_country_exposure() -> None:
    holdings = [_h("US_EQ_AAPL", 3000), _h("KR_EQ_005930", 1000)]
    attrs = {
        "US_EQ_AAPL": AssetAttrs(asset_type="equity", country="US"),
        "KR_EQ_005930": AssetAttrs(asset_type="equity", country="KR"),
    }

    countries = _by_type(_exposures(holdings, attrs), "country")

    assert abs(countries["US"].weight - 0.75) < 1e-9
    assert abs(countries["KR"].weight - 0.25) < 1e-9


def test_currency_exposure_uses_cash_row_currency() -> None:
    holdings = [_h("CASH_USD", 1000, currency="USD")]

    currencies = _by_type(
        _exposures(holdings, {}, base_currency="EUR"), "currency"
    )

    assert set(currencies) == {"USD"}
    assert abs(currencies["USD"].weight - 1.0) < 1e-9


# -- limit checks --------------------------------------------------------------


def test_exposure_violation_compares_against_profile_limits() -> None:
    holdings = [_h("US_EQ_AAPL", 8000), _h("CASH_USD", 2000)]
    attrs = {"US_EQ_AAPL": AssetAttrs(asset_type="equity", sector="Technology")}
    limits = ExposureLimits(
        max_single_position_weight=0.10,
        max_sector_weight=0.35,
    )

    exposures = _exposures(holdings, attrs, limits)
    positions = _by_type(exposures, "position")
    sectors = _by_type(exposures, "sector")

    # AAPL is 80% of the book — clearly over both the 10% position and 35% sector cap
    assert positions["US_EQ_AAPL"].is_violation is True
    assert positions["US_EQ_AAPL"].limit_weight == 0.10
    assert sectors["Technology"].is_violation is True
    # cash position (20%) is under the 10%? no — but it has no sector cap breach
    assert positions["CASH_USD"].is_violation is True  # 20% > 10%


def test_exposure_no_violation_when_under_limit_or_no_limit() -> None:
    holdings = [_h("US_EQ_AAPL", 5000), _h("US_EQ_MSFT", 5000)]
    attrs = {
        "US_EQ_AAPL": AssetAttrs(asset_type="equity", sector="Technology"),
        "US_EQ_MSFT": AssetAttrs(asset_type="equity", sector="Healthcare"),
    }
    # no position limit set -> never a violation; sector at 50% under a 60% cap
    limits = ExposureLimits(max_sector_weight=0.60)

    exposures = _exposures(holdings, attrs, limits)

    assert all(not e.is_violation for e in exposures if e.exposure_type == "position")
    sectors = _by_type(exposures, "sector")
    assert sectors["Technology"].is_violation is False


# -- policy drift --------------------------------------------------------------


def _default_targets() -> list[PolicyTarget]:
    return [
        PolicyTarget(
            "default", "core_us_equity", 0.55, 0.45, 0.65,
            metadata={"asset_types": ["etf"], "theme_tags": ["broad_market"]},
        ),
        PolicyTarget(
            "default", "satellite_equity", 0.15, 0.00, 0.20,
            metadata={"asset_types": ["equity"]},
        ),
        PolicyTarget(
            "default", "defensive_bonds", 0.20, 0.10, 0.30,
            metadata={"asset_types": ["bond_etf"]},
        ),
        PolicyTarget(
            "default", "cash", 0.10, 0.05, 0.20,
            metadata={"asset_ids": ["CASH_USD"]},
        ),
    ]


def test_policy_drift_identifies_sleeve_outside_band() -> None:
    # AAPL=80%, VOO=10%, BND=5%, CASH=5%  -> each maps to exactly one sleeve
    holdings = [
        _h("US_EQ_AAPL", 8000),
        _h("ETF_VOO", 1000),
        _h("ETF_BND", 500),
        _h("CASH_USD", 500),
    ]
    attrs = {
        "US_EQ_AAPL": AssetAttrs(asset_type="equity"),
        "ETF_VOO": AssetAttrs(asset_type="etf", theme_tags=["broad_market"]),
        "ETF_BND": AssetAttrs(asset_type="bond_etf"),
    }

    result = compute_policy_drifts(
        holdings, attrs, _default_targets(), portfolio_id="default", as_of_date=AS_OF
    )
    drifts = {d.sleeve_name: d for d in result.drifts}

    assert result.warnings == []  # every holding matched a sleeve cleanly
    sat = drifts["satellite_equity"]
    assert abs(sat.current_weight - 0.80) < 1e-9
    assert abs(sat.drift - (0.80 - 0.15)) < 1e-9
    assert sat.is_outside_band is True  # 0.80 > max 0.20

    core = drifts["core_us_equity"]
    assert abs(core.current_weight - 0.10) < 1e-9
    assert core.is_outside_band is True  # 0.10 < min 0.45

    cash = drifts["cash"]
    assert abs(cash.current_weight - 0.05) < 1e-9
    assert cash.is_outside_band is False  # 0.05 within [0.05, 0.20]


def test_policy_drift_unmatched_holding_falls_back_with_warning() -> None:
    # an asset whose type matches no sleeve -> fallback to satellite_equity + warn
    holdings = [_h("CMDTY_GLD", 1000)]
    attrs = {"CMDTY_GLD": AssetAttrs(asset_type="commodity_etf")}

    result = compute_policy_drifts(
        holdings, attrs, _default_targets(), portfolio_id="default", as_of_date=AS_OF
    )
    drifts = {d.sleeve_name: d for d in result.drifts}

    assert any("CMDTY_GLD" in w for w in result.warnings)
    assert abs(drifts["satellite_equity"].current_weight - 1.0) < 1e-9


def test_policy_drift_emits_row_for_empty_sleeve() -> None:
    holdings = [_h("CASH_USD", 1000)]

    result = compute_policy_drifts(
        holdings, {}, _default_targets(), portfolio_id="default", as_of_date=AS_OF
    )
    drifts = {d.sleeve_name: d for d in result.drifts}

    # every defined sleeve gets a row, even ones with no holdings
    assert set(drifts) == {"core_us_equity", "satellite_equity", "defensive_bonds", "cash"}
    core = drifts["core_us_equity"]
    assert core.current_weight == 0.0
    assert abs(core.drift - (0.0 - 0.55)) < 1e-9
    assert core.is_outside_band is True  # 0.0 < min 0.45

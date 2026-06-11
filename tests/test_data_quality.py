"""Sprint 008a: asset-type routing, FX integrity, and data-quality issues."""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from croesus.assets.classifier import PRICEABLE_ASSET_TYPES, classify_asset_type
from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.fx.convert import FxRateMissing, to_base
from croesus.jobs.backfill_asset_types import backfill_asset_types
from croesus.portfolio.mark_to_market import mark_to_market
from croesus.portfolio.models import Holding
from croesus.quality.models import SEVERITY_ERROR, DataQualityIssue
from croesus.quality.report_block import data_quality_block
from croesus.quality.repository import DataQualityRepository

AS_OF = date(2026, 6, 1)


def _asset(asset_id: str, symbol: str, name: str, asset_type: str, **kwargs) -> Asset:
    return Asset(
        asset_id=asset_id, symbol=symbol, name=name, asset_type=asset_type, **kwargs
    )


# ── classifier ────────────────────────────────────────────────────────────────

def test_classifier_refines_bond_reit_leveraged_and_crypto() -> None:
    agg = _asset("US_ETF_AGG", "AGG", "iShares Core U.S. Aggregate Bond ETF", "etf")
    vnq = _asset("US_ETF_VNQ", "VNQ", "Vanguard Real Estate ETF", "etf")
    tqqq = _asset("US_ETF_TQQQ", "TQQQ", "ProShares UltraPro QQQ", "etf")
    spy = _asset("US_ETF_SPY", "SPY", "SPDR S&P 500 ETF Trust", "etf")
    btc = _asset("US_CRYPTO_BTC", "BTC-USD", "Bitcoin USD", "cryptocurrency")

    assert classify_asset_type(agg) == "bond_etf"
    assert classify_asset_type(vnq) == "reit_etf"
    assert classify_asset_type(tqqq) == "leveraged_etf"
    assert classify_asset_type(spy) == "etf"
    assert classify_asset_type(btc) == "crypto"


def test_classifier_uses_yfinance_category_metadata() -> None:
    # Name alone is ambiguous; the yfinance category resolves it.
    asset = _asset(
        "US_ETF_SCHZ", "SCHZ", "Schwab US TIPS", "etf",
        metadata={"category": "Intermediate Core Bond"},
    )
    assert classify_asset_type(asset) == "bond_etf"


def test_leveraged_outranks_bond_keywords() -> None:
    tmf = _asset("US_ETF_TMF", "TMF", "Direxion Daily 20+ Year Treasury Bull 3X", "etf")
    assert classify_asset_type(tmf) == "leveraged_etf"


def test_priceable_types_exclude_cash_and_options() -> None:
    assert "cash" not in PRICEABLE_ASSET_TYPES
    assert "option" not in PRICEABLE_ASSET_TYPES
    assert {"equity", "etf", "bond_etf", "crypto"} <= PRICEABLE_ASSET_TYPES


# ── backfill job ──────────────────────────────────────────────────────────────

def test_backfill_reclassifies_existing_rows_idempotently(tmp_path: Path) -> None:
    db_path = tmp_path / "b.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        AssetRepository(conn).upsert_many(
            [
                _asset("US_ETF_AGG", "AGG", "iShares Core U.S. Aggregate Bond ETF", "etf"),
                _asset("US_ETF_SPY", "SPY", "SPDR S&P 500 ETF Trust", "etf"),
            ]
        )
        changed = backfill_asset_types(conn, log=lambda m: None)
        assert changed == {"US_ETF_AGG": "bond_etf"}

        # asset_id is never rewritten; only asset_type changes.
        row = conn.execute(
            "SELECT asset_type FROM assets WHERE asset_id = 'US_ETF_AGG'"
        ).fetchone()
        assert row[0] == "bond_etf"

        assert backfill_asset_types(conn, log=lambda m: None) == {}


# ── FX conversion ─────────────────────────────────────────────────────────────

def test_to_base_raises_on_missing_rate_unless_opted_in() -> None:
    with pytest.raises(FxRateMissing):
        to_base(100.0, native_currency="KRW", base_currency="USD", rates={"USD": 1.0})

    value = to_base(
        100.0,
        native_currency="KRW",
        base_currency="USD",
        rates={"USD": 1.0},
        fallback_to_one=True,
    )
    assert value == 100.0  # explicit, audited passthrough only


# ── mark_to_market issues ─────────────────────────────────────────────────────

def _holding(asset_id: str, **overrides) -> Holding:
    defaults = dict(
        portfolio_id="default",
        asset_id=asset_id,
        as_of_date=AS_OF,
        quantity=1.0,
        market_value=None,
        currency="USD",
    )
    defaults.update(overrides)
    return Holding(**defaults)


def test_missing_price_records_error_issue() -> None:
    result = mark_to_market(
        [_holding("US_EQ_AAPL", quantity=2.0, avg_cost=100.0)],
        price_lookup=lambda _aid: None,
        fx_rates={"USD": 1.0},
        assets_by_id={},
        base_currency="USD",
        as_of_date=AS_OF,
    )
    errors = [i for i in result.issues if i.severity == SEVERITY_ERROR]
    assert len(errors) == 1
    assert errors[0].code == "PRICE_MISSING"
    assert errors[0].asset_id == "US_EQ_AAPL"
    assert result.total_market_value == 200.0  # cost-basis fallback, but loudly


def test_missing_fx_records_error_and_flags_holding() -> None:
    result = mark_to_market(
        [_holding("CASH_KRW", quantity=0.0, market_value=1_000_000.0, currency="KRW")],
        price_lookup=lambda _aid: None,
        fx_rates={"USD": 1.0},  # no KRW rate
        assets_by_id={},
        base_currency="USD",
        as_of_date=AS_OF,
    )
    errors = [i for i in result.issues if i.severity == SEVERITY_ERROR]
    assert [e.code for e in errors] == ["FX_MISSING"]
    assert errors[0].currency == "KRW"
    assert result.holdings[0].metadata["fx_missing"] is True


def test_present_fx_converts_without_issues() -> None:
    result = mark_to_market(
        [_holding("CASH_KRW", quantity=0.0, market_value=1_400_000.0, currency="KRW")],
        price_lookup=lambda _aid: None,
        fx_rates={"USD": 1.0, "KRW": 1400.0},
        assets_by_id={},
        base_currency="USD",
        as_of_date=AS_OF,
    )
    assert result.issues == []
    assert abs(result.total_market_value - 1000.0) < 1e-9


# ── snapshot integration: on-demand FX + persisted issues ─────────────────────

class _FxAwarePriceSource:
    """Serves daily closes for FX symbols only (e.g. 'KRW=X')."""

    source_name = "fake"

    def __init__(self, fx_rate: float | None) -> None:
        self.fx_rate = fx_rate

    def fetch_daily_prices(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        columns = ["date", "open", "high", "low", "close", "adjusted_close", "volume"]
        if symbol.endswith("=X") and self.fx_rate is not None:
            rate = self.fx_rate
            return pd.DataFrame(
                [{"date": AS_OF, "open": rate, "high": rate, "low": rate,
                  "close": rate, "adjusted_close": rate, "volume": 0}]
            )
        return pd.DataFrame(columns=columns)


def _snapshot_with_krw_cash(tmp_path: Path, fx_rate: float | None):
    from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot
    from croesus.profiles.seed_default_profile import seed_default_profile

    db_path = tmp_path / "k.duckdb"
    migrate(db_path)
    csv_path = tmp_path / "h.csv"
    csv_path.write_text(
        "portfolio_id,asset_id,quantity,market_value,currency,cost_basis\n"
        "default,CASH_KRW,1,1400000,KRW,1400000\n"
        "default,CASH_USD,1,1000,USD,1000\n",
        encoding="utf-8",
    )
    with get_connection(db_path) as conn:
        seed_default_profile(conn)
        result = run_portfolio_snapshot(
            conn,
            csv_path,
            as_of_date=AS_OF,
            price_source=_FxAwarePriceSource(fx_rate),
            log=lambda m: None,
        )
        persisted = DataQualityRepository(conn).list_recent()
    return result, persisted


def test_first_snapshot_fetches_krw_rate_on_demand(tmp_path: Path) -> None:
    result, persisted = _snapshot_with_krw_cash(tmp_path, fx_rate=1400.0)
    # 1,400,000 KRW at 1400/USD = 1,000 USD — not 1,400,000 USD.
    assert abs(result.total_market_value - 2000.0) < 1e-9
    assert result.data_quality_errors == []
    assert persisted == []


def test_unfetchable_krw_rate_degrades_snapshot_loudly(tmp_path: Path) -> None:
    result, persisted = _snapshot_with_krw_cash(tmp_path, fx_rate=None)
    assert [i.code for i in result.data_quality_errors] == ["FX_MISSING"]
    # The issue is persisted, not just printed.
    assert [i.code for i in persisted] == ["FX_MISSING"]
    assert persisted[0].currency == "KRW"


# ── report block ──────────────────────────────────────────────────────────────

def test_data_quality_block_renders_recent_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        assert data_quality_block(conn) == []
        DataQualityRepository(conn).record_many(
            [
                DataQualityIssue(
                    domain="portfolio_snapshot",
                    severity=SEVERITY_ERROR,
                    code="FX_MISSING",
                    message="no KRW rate",
                    currency="KRW",
                    as_of_date=AS_OF,
                )
            ]
        )
        block = data_quality_block(conn)
    assert block[0] == "## ⚠️ Data Quality — DEGRADED"
    assert any("FX_MISSING" in line and "KRW" in line for line in block)

from __future__ import annotations

from datetime import date
from pathlib import Path

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.screening_run import run_screening_job
from croesus.macro._loader import store_macro_state
from croesus.macro.models import MacroState
from croesus.macro.screening_adapter import neutral_screening_params
from croesus.screening.normalization import percentile_rank
from croesus.screening.repository import ScreeningRepository
from croesus.screening.run_screening import run_screening

AS_OF = date(2026, 6, 1)


def test_percentile_rank_returns_zero_to_one_and_preserves_nulls() -> None:
    scores = percentile_rank({"a": 10.0, "b": 30.0, "c": None})

    assert scores["a"] == 0.0
    assert scores["b"] == 1.0
    assert scores["c"] is None
    assert all(value is None or 0.0 <= value <= 1.0 for value in scores.values())


def test_percentile_rank_handles_ties_with_average_percentile() -> None:
    scores = percentile_rank({"a": 10.0, "b": 20.0, "c": 20.0, "d": 40.0})

    assert scores["a"] == 0.0
    assert scores["b"] == 0.5
    assert scores["c"] == 0.5
    assert scores["d"] == 1.0


def test_screening_reads_factors_skips_inactive_and_persists_results(tmp_path: Path) -> None:
    db_path = tmp_path / "screening.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        _seed_assets(conn, include_inactive=True)
        _seed_factor_values(conn)

        result = run_screening(
            conn,
            neutral_screening_params() | {"candidate_count": 2},
            as_of_date=AS_OF,
        )

        persisted = ScreeningRepository(conn).list_results(result.run_id)

    assert [candidate.asset_id for candidate in result.candidates] == [
        "US_EQ_AAPL",
        "US_EQ_NVDA",
        "US_EQ_MSFT",
    ]
    assert {candidate.asset_id for candidate in result.skipped} == {"US_EQ_META"}
    assert all(candidate.asset_id != "US_EQ_OLD" for candidate in result.candidates)
    assert len(persisted) == 4
    assert persisted[0].factor_scores["momentum_score"] is not None
    assert persisted[0].metadata["portfolio_fit"] == "addable"


def test_screening_uses_neutral_weights_when_macro_state_absent(tmp_path: Path) -> None:
    db_path = tmp_path / "neutral.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        _seed_factor_values(conn)
        result = run_screening_job(conn, as_of_date=AS_OF, log=lambda message: None)

    assert result.screening_params["regime"] is None
    assert result.screening_params["factor_weights"]["momentum"] == 0.35
    assert result.candidates[0].asset_id == "US_EQ_AAPL"


def test_screening_uses_macro_state_weights_when_present(tmp_path: Path) -> None:
    db_path = tmp_path / "macro.duckdb"
    migrate(db_path)
    store_macro_state(_sample_macro_state(), db_path)

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        _seed_factor_values(conn)
        result = run_screening_job(conn, as_of_date=AS_OF, log=lambda message: None)

    assert result.screening_params["regime"] == "Goldilocks"
    assert result.screening_params["factor_weights"]["momentum"] > 0.35
    assert result.candidates[0].score is not None


def test_missing_factors_are_persisted_as_skipped(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        _seed_factor_values(conn, missing_msft=True)
        result = run_screening(
            conn,
            neutral_screening_params(),
            as_of_date=AS_OF,
        )

        skipped_rows = [
            row
            for row in ScreeningRepository(conn).list_results(result.run_id)
            if row.decision_bucket == "skipped"
        ]

    msft = next(row for row in skipped_rows if row.asset_id == "US_EQ_MSFT")
    assert "missing" in msft.reason
    assert "MISSING_MOMENTUM_FACTORS" in msft.reason_codes


def test_screening_applies_liquidity_and_volatility_filters(tmp_path: Path) -> None:
    db_path = tmp_path / "filters.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        _seed_factor_values(conn)
        params = neutral_screening_params() | {
            "filters": {
                "min_liquidity_usd": 42_000_000.0,
                "max_volatility_3m": 0.20,
            }
        }
        result = run_screening(conn, params, as_of_date=AS_OF)

    skipped = {candidate.asset_id: candidate for candidate in result.skipped}
    assert skipped["US_EQ_MSFT"].reason_codes == ["LIQUIDITY_BELOW_MINIMUM"]
    assert skipped["US_EQ_NVDA"].reason_codes == ["VOLATILITY_ABOVE_MAXIMUM"]


def test_overexposed_candidate_is_blocked_not_addable(tmp_path: Path) -> None:
    db_path = tmp_path / "blocked.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        _seed_factor_values(conn)
        conn.execute(
            """
            INSERT INTO portfolio_exposures (
              portfolio_id, as_of_date, exposure_type, exposure_name,
              weight, market_value, limit_weight, is_violation
            )
            VALUES ('default', ?, 'sector', 'Technology', 0.42, 42000, 0.35, TRUE)
            """,
            [AS_OF],
        )

        result = run_screening(
            conn,
            neutral_screening_params() | {"candidate_count": 2},
            as_of_date=AS_OF,
            portfolio_id="default",
        )

    aapl = next(candidate for candidate in result.candidates if candidate.asset_id == "US_EQ_AAPL")
    assert aapl.decision_bucket == "blocked_by_portfolio_fit"
    assert aapl.metadata["portfolio_fit"] == "blocked"
    assert aapl.metadata["blocking_exposures"] == ["sector:Technology"]
    assert aapl.metadata["would_worsen_violation"] is True


def _seed_assets(conn, *, include_inactive: bool = False) -> None:
    assets = [
        Asset(
            "US_EQ_AAPL",
            "AAPL",
            "Apple Inc.",
            "equity",
            country="US",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            industry="Consumer Electronics",
            source="test",
            metadata={"theme_tags": ["ai", "consumer"]},
        ),
        Asset(
            "US_EQ_MSFT",
            "MSFT",
            "Microsoft Corp.",
            "equity",
            country="US",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            industry="Software",
            source="test",
            metadata={"theme_tags": ["ai", "cloud"]},
        ),
        Asset(
            "US_EQ_NVDA",
            "NVDA",
            "NVIDIA Corp.",
            "equity",
            country="US",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            industry="Semiconductors",
            source="test",
            metadata={"theme_tags": ["ai", "semiconductor"]},
        ),
        Asset(
            "US_EQ_META",
            "META",
            "Meta Platforms",
            "equity",
            country="US",
            exchange="NASDAQ",
            currency="USD",
            sector="Communication Services",
            industry="Internet Content",
            source="test",
            metadata={"theme_tags": ["social"]},
        ),
    ]
    if include_inactive:
        assets.append(
            Asset(
                "US_EQ_OLD",
                "OLD",
                "Old Co.",
                "equity",
                country="US",
                exchange="NYSE",
                currency="USD",
                sector="Industrials",
                industry="Legacy",
                is_active=False,
                source="test",
                metadata={},
            )
        )
    AssetRepository(conn).upsert_many(assets)


def _seed_factor_values(conn, *, missing_msft: bool = False) -> None:
    factors_by_asset = {
        "US_EQ_AAPL": {
            "momentum_1m": 0.12,
            "momentum_3m": 0.24,
            "momentum_6m": 0.30,
            "liquidity_1m": 50_000_000.0,
            "above_200d_ma": 1.0,
            "volatility_3m": 0.18,
        },
        "US_EQ_NVDA": {
            "momentum_1m": 0.08,
            "momentum_3m": 0.18,
            "momentum_6m": 0.26,
            "liquidity_1m": 45_000_000.0,
            "above_200d_ma": 1.0,
            "volatility_3m": 0.26,
        },
        "US_EQ_MSFT": {
            "momentum_1m": 0.02,
            "momentum_3m": 0.07,
            "momentum_6m": 0.10,
            "liquidity_1m": 40_000_000.0,
            "above_200d_ma": 1.0,
            "volatility_3m": 0.16,
        },
        "US_EQ_META": {
            "momentum_1m": None,
            "momentum_3m": None,
            "momentum_6m": None,
            "liquidity_1m": 35_000_000.0,
            "above_200d_ma": 1.0,
            "volatility_3m": 0.20,
        },
        "US_EQ_OLD": {
            "momentum_1m": 0.50,
            "momentum_3m": 0.60,
            "momentum_6m": 0.70,
            "liquidity_1m": 60_000_000.0,
            "above_200d_ma": 1.0,
            "volatility_3m": 0.10,
        },
    }
    if missing_msft:
        factors_by_asset["US_EQ_MSFT"] = {
            "momentum_1m": None,
            "momentum_3m": None,
            "momentum_6m": None,
            "liquidity_1m": 40_000_000.0,
            "above_200d_ma": None,
            "volatility_3m": 0.16,
        }

    rows = [
        (asset_id, AS_OF, factor_name, value)
        for asset_id, factors in factors_by_asset.items()
        for factor_name, value in factors.items()
    ]
    conn.executemany(
        """
        INSERT INTO factor_values (asset_id, date, factor_name, value)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )


def _sample_macro_state() -> MacroState:
    return MacroState(
        date=AS_OF,
        regime="Goldilocks",
        regime_confidence=0.8,
        growth_direction="Expanding",
        inflation_direction="Falling",
        amplifier_score=25.0,
        confirmation_score=0.2,
        positioning="Aggressive",
        warnings=[],
        opportunities=[],
        raw_indicators={},
        regime_methods={},
    )

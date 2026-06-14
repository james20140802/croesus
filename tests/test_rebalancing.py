from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.rebalance_check import run_rebalance_check
from croesus.portfolio.actions import ProposedAction, RebalanceRunResult
from croesus.portfolio.models import AssetAttrs, Exposure, Holding, PolicyDrift, Portfolio
from croesus.portfolio.rebalancing import generate_proposed_actions
from croesus.portfolio.repository import PortfolioRepository
from croesus.profiles.models import AssetType, Currency, InvestorProfile, TradeMode
from croesus.profiles.repository import ProfileRepository
from croesus.screening.models import ScreeningCandidate

AS_OF = date(2026, 6, 1)


def test_migrate_creates_rebalance_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "rebalance.duckdb"

    migrate(db_path)

    with get_connection(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }

    assert {"rebalance_runs", "proposed_actions"} <= tables


def test_rebalance_action_models_capture_product_contract(tmp_path: Path) -> None:
    action = ProposedAction(
        action_id="act-1",
        run_id="run-1",
        asset_id="US_EQ_NVDA",
        sleeve_name=None,
        action_type="trim",
        current_weight=0.18,
        target_weight=0.10,
        proposed_weight=0.10,
        estimated_trade_value=8000.0,
        reason_codes=["POSITION_OVER_MAX"],
        human_readable_reason="Trim US_EQ_NVDA from 18.0% to 10.0%.",
        requires_research=False,
        requires_user_approval=True,
    )
    result = RebalanceRunResult(
        run_id="run-1",
        portfolio_id="default",
        profile_id="default",
        as_of_date=AS_OF,
        decision="rebalance_recommended",
        actions=[action],
        markdown_report_path=tmp_path / "portfolio_action_2026-06-01.md",
        csv_report_path=tmp_path / "portfolio_action_2026-06-01.csv",
    )

    assert result.actions == [action]
    assert action.reason_codes == ["POSITION_OVER_MAX"]
    assert action.requires_user_approval is True


def test_portfolio_repository_persists_rebalance_run_and_actions(tmp_path: Path) -> None:
    db_path = tmp_path / "rebalance.duckdb"
    migrate(db_path)
    action = _action(
        "act-1",
        "run-1",
        action_type="trim",
        asset_id="US_EQ_NVDA",
        current_weight=0.18,
        proposed_weight=0.10,
        estimated_trade_value=8000.0,
        reason_codes=["POSITION_OVER_MAX"],
    )

    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.upsert_rebalance_run(
            "run-1",
            "default",
            "default",
            AS_OF,
            decision="rebalance_recommended",
            summary="1 action proposed.",
            macro_regime="Goldilocks",
            macro_positioning="Neutral",
            metadata={"screening_run_id": "screen-1"},
        )
        repo.replace_proposed_actions("run-1", [action])
        loaded = repo.get_rebalance_run("run-1")

    assert loaded is not None
    assert loaded["run_id"] == "run-1"
    assert loaded["decision"] == "rebalance_recommended"
    assert loaded["metadata"] == {"screening_run_id": "screen-1"}
    # Persisting stamps the approval gate (Sprint 011); everything else
    # round-trips unchanged.
    [persisted] = loaded["actions"]
    assert persisted.approval_status == "pending"
    assert persisted.expires_at is not None
    assert replace(persisted, approval_status=None, expires_at=None) == action


def test_load_latest_rebalance_run_prefers_newest_date(tmp_path: Path) -> None:
    db_path = tmp_path / "rebalance.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.upsert_rebalance_run(
            "run-old",
            "default",
            "default",
            date(2026, 5, 31),
            decision="no_action",
            summary="No action.",
            metadata={},
        )
        repo.replace_proposed_actions(
            "run-old",
            [_action("act-old", "run-old", action_type="hold")],
        )
        repo.upsert_rebalance_run(
            "run-new",
            "default",
            "default",
            AS_OF,
            decision="rebalance_recommended",
            summary="1 action proposed.",
            metadata={},
        )
        repo.replace_proposed_actions(
            "run-new",
            [_action("act-new", "run-new", action_type="raise_cash")],
        )
        loaded = repo.load_latest_rebalance_run("default")

    assert loaded is not None
    assert loaded["run_id"] == "run-new"
    assert [action.action_id for action in loaded["actions"]] == ["act-new"]


def test_invalid_profile_returns_profile_invalid_and_no_trade_actions() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(expected_annual_return=-0.01),
        total_market_value=100_000.0,
        exposures=[_position("US_EQ_NVDA", 0.30, 0.10)],
        drifts=[_drift("satellite_equity", 0.30, 0.15, 0.00, 0.20)],
        holdings=[_holding("US_EQ_NVDA", 30_000.0)],
        assets_by_id={"US_EQ_NVDA": AssetAttrs(asset_type="equity", sector="Technology")},
        screening_candidates=[
            _candidate("US_EQ_MSFT", score=0.95, metadata={"sleeve_name": "satellite_equity"})
        ],
    )

    assert [action.action_type for action in actions] == ["hold"]
    assert actions[0].reason_codes == ["PROFILE_INVALID"]


def test_position_over_max_creates_trim() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(max_single_position_weight=0.10),
        total_market_value=100_000.0,
        exposures=[_position("US_EQ_NVDA", 0.18, 0.10)],
        holdings=[_holding("US_EQ_NVDA", 18_000.0)],
        assets_by_id={"US_EQ_NVDA": AssetAttrs(asset_type="equity", sector="Technology")},
    )

    trim = _one(actions, "trim")
    assert trim.asset_id == "US_EQ_NVDA"
    assert trim.current_weight == 0.18
    assert trim.proposed_weight == 0.10
    assert trim.estimated_trade_value == 8_000.0
    assert trim.reason_codes == ["POSITION_OVER_MAX"]


def test_sector_over_max_creates_block_new_buy() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(max_sector_weight=0.35),
        total_market_value=100_000.0,
        exposures=[_exposure("sector", "Technology", 0.40, 0.35)],
    )

    block = _one(actions, "block_new_buy")
    assert block.sleeve_name == "Technology"
    assert block.reason_codes == ["SECTOR_OVER_MAX"]


def test_severe_sector_over_max_also_trims_largest_holding_in_sector() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(max_sector_weight=0.35, rebalance_band=0.05),
        total_market_value=100_000.0,
        exposures=[_exposure("sector", "Technology", 0.47, 0.35)],
        holdings=[
            _holding("US_EQ_AAPL", 20_000.0),
            _holding("US_EQ_NVDA", 27_000.0),
            _holding("US_EQ_JNJ", 10_000.0),
        ],
        assets_by_id={
            "US_EQ_AAPL": AssetAttrs(asset_type="equity", sector="Technology"),
            "US_EQ_NVDA": AssetAttrs(asset_type="equity", sector="Technology"),
            "US_EQ_JNJ": AssetAttrs(asset_type="equity", sector="Healthcare"),
        },
    )

    trims = [action for action in actions if action.action_type == "trim"]
    assert trims[0].asset_id == "US_EQ_NVDA"
    assert "SECTOR_OVER_MAX" in trims[0].reason_codes


def test_redundancy_group_over_max_blocks_buys_and_trims_largest_member() -> None:
    # GOOG 8% + GOOGL 6% = 14% combined Alphabet, over the 10% single-name cap.
    # The group over-exposure must block new Alphabet buys and trim the larger
    # of the two classes (GOOG), even though neither class trips on its own.
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(max_single_position_weight=0.10, rebalance_band=0.02),
        total_market_value=100_000.0,
        exposures=[_exposure("redundancy_group", "issuer:alphabet", 0.14, 0.10)],
        holdings=[
            _holding("US_EQ_GOOG", 8_000.0),
            _holding("US_EQ_GOOGL", 6_000.0),
            _holding("US_EQ_AAPL", 86_000.0),
        ],
        assets_by_id={
            "US_EQ_GOOG": AssetAttrs(
                asset_type="equity", name="Alphabet Inc. (Class C)"
            ),
            "US_EQ_GOOGL": AssetAttrs(asset_type="equity", name="Alphabet Inc."),
            "US_EQ_AAPL": AssetAttrs(asset_type="equity", name="Apple Inc."),
        },
    )

    block = _one(actions, "block_new_buy")
    assert block.sleeve_name == "issuer:alphabet"
    assert block.reason_codes == ["REDUNDANT_GROUP_OVER_MAX"]

    trims = [a for a in actions if a.action_type == "trim"]
    assert trims[0].asset_id == "US_EQ_GOOG"  # the larger Alphabet class
    assert "REDUNDANT_GROUP_OVER_MAX" in trims[0].reason_codes


def test_sleeve_under_min_creates_rebalance_to_band() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(max_monthly_turnover=0.50),
        total_market_value=100_000.0,
        drifts=[_drift("core_us_equity", 0.30, 0.55, 0.45, 0.65)],
    )

    action = _one(actions, "rebalance_to_band")
    assert action.sleeve_name == "core_us_equity"
    assert action.current_weight == 0.30
    assert action.proposed_weight == 0.55
    assert action.estimated_trade_value == 25_000.0
    assert action.reason_codes == ["SLEEVE_UNDER_BAND"]


def test_cash_under_min_creates_raise_cash_and_blocks_adds() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(),
        total_market_value=100_000.0,
        drifts=[_drift("cash", 0.02, 0.10, 0.05, 0.20)],
        screening_candidates=[
            _candidate("US_EQ_MSFT", score=0.98, metadata={"sleeve_name": "satellite_equity"})
        ],
    )

    assert _one(actions, "raise_cash").reason_codes == ["CASH_BELOW_BUFFER"]
    assert not any(action.action_type == "add" for action in actions)


def test_cautious_macro_blocks_new_satellite_adds() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(),
        total_market_value=100_000.0,
        drifts=[_drift("satellite_equity", 0.10, 0.15, 0.00, 0.20)],
        screening_candidates=[
            _candidate("US_EQ_MSFT", score=0.98, metadata={"sleeve_name": "satellite_equity"})
        ],
        macro_state=_macro("Cautious"),
    )

    watch = _one(actions, "watch")
    assert watch.asset_id == "US_EQ_MSFT"
    assert "MACRO_CAUTIOUS_TIGHTEN_RISK" in watch.reason_codes


def test_defensive_macro_keeps_concentration_reduction_before_candidate_watch() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(max_single_position_weight=0.10),
        total_market_value=100_000.0,
        exposures=[_position("US_EQ_NVDA", 0.18, 0.10)],
        screening_candidates=[
            _candidate("US_EQ_MSFT", score=0.98, metadata={"sleeve_name": "satellite_equity"})
        ],
        macro_state=_macro("Defensive"),
    )

    assert actions[0].action_type == "trim"
    assert "MACRO_DEFENSIVE_REDUCE_CONCENTRATION" in actions[0].reason_codes
    assert _one(actions, "watch").action_type == "watch"


def test_candidate_add_created_when_policy_macro_and_exposure_allow() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(max_monthly_turnover=0.30),
        total_market_value=100_000.0,
        drifts=[_drift("satellite_equity", 0.10, 0.15, 0.00, 0.20)],
        screening_candidates=[
            _candidate("US_EQ_MSFT", score=0.98, metadata={"sleeve_name": "satellite_equity"})
        ],
        macro_state=_macro("Neutral"),
    )

    add = _one(actions, "add")
    assert add.asset_id == "US_EQ_MSFT"
    assert add.sleeve_name == "satellite_equity"
    assert add.estimated_trade_value == 5_000.0
    assert add.reason_codes == ["FACTOR_SCORE_SUPPORTS_ADD"]


def test_blocked_high_scoring_candidate_becomes_watch_not_add() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(),
        total_market_value=100_000.0,
        drifts=[_drift("satellite_equity", 0.10, 0.15, 0.00, 0.20)],
        screening_candidates=[
            _candidate(
                "US_EQ_MSFT",
                score=0.98,
                decision_bucket="blocked_by_portfolio_fit",
                metadata={
                    "sleeve_name": "satellite_equity",
                    "blocking_exposures": ["sector:Technology"],
                },
            )
        ],
    )

    watch = _one(actions, "watch")
    assert watch.asset_id == "US_EQ_MSFT"
    assert not any(action.action_type == "add" for action in actions)


def test_turnover_limit_drops_lower_priority_adds_and_marks_affected_actions() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(max_single_position_weight=0.10, max_monthly_turnover=0.05),
        total_market_value=100_000.0,
        exposures=[_position("US_EQ_NVDA", 0.18, 0.10)],
        drifts=[_drift("satellite_equity", 0.10, 0.15, 0.00, 0.20)],
        screening_candidates=[
            _candidate("US_EQ_MSFT", score=0.98, metadata={"sleeve_name": "satellite_equity"})
        ],
    )

    trim = _one(actions, "trim")
    assert "TURNOVER_LIMIT" in trim.reason_codes
    assert trim.estimated_trade_value == 5_000.0
    assert not any(action.action_type == "add" for action in actions)


def test_no_violations_creates_hold() -> None:
    actions = generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(),
        total_market_value=100_000.0,
    )

    assert [action.action_type for action in actions] == ["hold"]
    assert actions[0].reason_codes == ["NO_ACTION_WITHIN_POLICY"]


def test_run_rebalance_check_persists_actions_and_returns_result(tmp_path: Path) -> None:
    db_path = tmp_path / "rebalance.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        _seed_rebalance_state(conn)
        result = run_rebalance_check(
            conn,
            portfolio_id="default",
            profile_id="default",
            as_of_date=AS_OF,
            reports_dir=tmp_path,
            log=lambda message: None,
        )
        latest = PortfolioRepository(conn).load_latest_rebalance_run("default")

    assert result.decision == "rebalance_recommended"
    assert [action.action_type for action in result.actions] == ["trim"]
    assert result.markdown_report_path is not None
    assert result.csv_report_path is not None
    assert latest is not None
    assert latest["run_id"] == result.run_id
    # The persisted copies additionally carry the approval-gate stamp.
    assert [
        replace(a, approval_status=None, expires_at=None) for a in latest["actions"]
    ] == result.actions
    assert all(a.approval_status == "pending" for a in latest["actions"])


def test_run_rebalance_check_does_not_submit_or_prepare_broker_orders(tmp_path: Path) -> None:
    db_path = tmp_path / "rebalance.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        _seed_rebalance_state(conn)
        result = run_rebalance_check(
            conn,
            portfolio_id="default",
            profile_id="default",
            as_of_date=AS_OF,
            reports_dir=tmp_path,
            log=lambda message: None,
        )
        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }

    assert "broker_orders" not in tables
    assert "orders" not in tables
    assert all("order" not in action.action_type for action in result.actions)


def _action(
    action_id: str,
    run_id: str,
    *,
    action_type: str,
    asset_id: str | None = None,
    sleeve_name: str | None = None,
    current_weight: float | None = None,
    target_weight: float | None = None,
    proposed_weight: float | None = None,
    estimated_trade_value: float | None = None,
    reason_codes: list[str] | None = None,
    human_readable_reason: str = "Reason.",
) -> ProposedAction:
    return ProposedAction(
        action_id=action_id,
        run_id=run_id,
        asset_id=asset_id,
        sleeve_name=sleeve_name,
        action_type=action_type,
        current_weight=current_weight,
        target_weight=target_weight,
        proposed_weight=proposed_weight,
        estimated_trade_value=estimated_trade_value,
        reason_codes=reason_codes or ["NO_ACTION_WITHIN_POLICY"],
        human_readable_reason=human_readable_reason,
        requires_research=False,
        requires_user_approval=True,
    )


def _profile(**overrides) -> InvestorProfile:
    fields = dict(
        profile_id="default",
        name="Default profile",
        base_currency=Currency.USD,
        expected_annual_return=0.08,
        max_tolerable_drawdown=-0.25,
        investment_horizon_years=10,
        monthly_contribution=1000.0,
        liquidity_buffer_months=6.0,
        allowed_asset_types=[AssetType.EQUITY, AssetType.ETF, AssetType.CASH],
        disallowed_asset_types=[],
        max_single_position_weight=0.10,
        max_sector_weight=0.35,
        max_industry_weight=0.25,
        max_theme_weight=0.25,
        max_country_weight=0.80,
        max_currency_weight=0.90,
        max_monthly_turnover=0.20,
        rebalance_band=0.05,
        trade_mode=TradeMode.PROPOSE_ONLY,
        metadata={},
    )
    fields.update(overrides)
    return InvestorProfile(**fields)


def _position(asset_id: str, weight: float, limit: float) -> Exposure:
    return _exposure("position", asset_id, weight, limit)


def _exposure(exposure_type: str, name: str, weight: float, limit: float) -> Exposure:
    return Exposure(
        portfolio_id="default",
        as_of_date=AS_OF,
        exposure_type=exposure_type,
        exposure_name=name,
        weight=weight,
        market_value=weight * 100_000.0,
        limit_weight=limit,
        is_violation=True,
    )


def _drift(
    sleeve_name: str,
    current: float,
    target: float,
    min_weight: float | None,
    max_weight: float | None,
) -> PolicyDrift:
    return PolicyDrift(
        portfolio_id="default",
        as_of_date=AS_OF,
        sleeve_name=sleeve_name,
        current_weight=current,
        target_weight=target,
        min_weight=min_weight,
        max_weight=max_weight,
        drift=current - target,
        is_outside_band=True,
    )


def _holding(asset_id: str, market_value: float) -> Holding:
    return Holding("default", asset_id, AS_OF, 1.0, market_value, "USD")


def _candidate(
    asset_id: str,
    *,
    score: float,
    decision_bucket: str = "candidate",
    metadata: dict | None = None,
) -> ScreeningCandidate:
    return ScreeningCandidate(
        run_id="screen-1",
        asset_id=asset_id,
        score=score,
        rank=1,
        decision_bucket=decision_bucket,
        reason="passes screen",
        reason_codes=[],
        factor_scores={"strategy_score": score},
        metadata=metadata or {},
    )


def _macro(positioning: str):
    from croesus.macro.models import MacroState

    return MacroState(
        date=AS_OF,
        regime="Goldilocks",
        regime_confidence=0.7,
        growth_direction="Expanding",
        inflation_direction="Falling",
        amplifier_score=25.0,
        confirmation_score=0.5,
        positioning=positioning,
    )


def _one(actions: list[ProposedAction], action_type: str) -> ProposedAction:
    found = [action for action in actions if action.action_type == action_type]
    assert len(found) == 1
    return found[0]


def _seed_rebalance_state(conn) -> None:
    profile = _profile(max_single_position_weight=0.10, max_monthly_turnover=0.50)
    ProfileRepository(conn).upsert_profile(profile)
    repo = PortfolioRepository(conn)
    repo.upsert_portfolio(
        Portfolio(
            portfolio_id="default",
            profile_id="default",
            name="Default",
            base_currency="USD",
        )
    )
    repo.save_snapshot("default", AS_OF, 100_000.0, cash_value=10_000.0)
    repo.replace_holdings(
        "default",
        AS_OF,
        [_holding("US_EQ_NVDA", 18_000.0), _holding("CASH_USD", 10_000.0)],
    )
    repo.replace_exposures("default", AS_OF, [_position("US_EQ_NVDA", 0.18, 0.10)])
    repo.replace_drifts("default", AS_OF, [])

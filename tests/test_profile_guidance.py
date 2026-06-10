import pytest

from croesus.profiles import guidance as gmod
from croesus.profiles.guidance import (
    ABOVE_HIGHEST,
    Guardrails,
    RiskBand,
    HistoricalEpisode,
    anchor_on_drawdown,
    anchor_on_return,
    apply_guidance_to_profile,
    apply_resolution_to_profile,
    detect_conflict,
)


_TEST_GUARDRAILS = Guardrails(
    liquidity_buffer_months=9.0,
    max_single_position_weight=0.04,
    max_sector_weight=0.18,
    max_industry_weight=0.12,
    max_theme_weight=0.12,
    max_country_weight=0.65,
    max_currency_weight=0.75,
    max_monthly_turnover=0.04,
    rebalance_band=0.02,
)
from croesus.profiles.policy_templates import POLICY_TEMPLATES
from croesus.profiles.seed_default_profile import DEFAULT_PROFILE
from croesus.profiles.validation import validate_profile


# --- AC1: return anchor produces complete, consistent guidance ----------------


def test_anchor_on_return_capital_preservation_band():
    g = anchor_on_return(0.03)
    assert g.anchor == "return"
    assert g.matched_band == "capital_preservation"
    assert g.implied_drawdown_range == pytest.approx((-0.15, -0.05))
    assert g.min_recommended_horizon_years == 2
    assert g.template_id == "capital_preservation"
    assert g.conflicts == []
    assert g.warnings == []


def test_anchor_on_return_balanced_band():
    g = anchor_on_return(0.05)
    assert g.matched_band == "balanced"
    assert g.template_id == "balanced_long_term"
    assert g.min_recommended_horizon_years == 5


def test_anchor_on_return_growth_band():
    g = anchor_on_return(0.075)
    assert g.matched_band == "growth"
    assert g.template_id == "growth_long_term"


def test_anchor_on_return_equity_max_band_shares_growth_template():
    g = anchor_on_return(0.10)
    assert g.matched_band == "equity_max"
    assert g.template_id == "growth_long_term"


# --- AC2: drawdown anchor yields a consistent, non-contradictory ceiling -------


def test_anchor_on_drawdown_returns_realistic_ceiling():
    g = anchor_on_drawdown(-0.08)
    assert g.anchor == "drawdown"
    assert g.matched_band == "capital_preservation"
    assert g.implied_return_range[1] <= 0.04


def test_anchor_directions_never_contradict_within_a_band():
    r = anchor_on_return(0.05)  # balanced
    d = anchor_on_drawdown(-0.20)  # balanced
    assert r.matched_band == d.matched_band == "balanced"


def test_anchor_on_drawdown_deepest_band():
    g = anchor_on_drawdown(-0.50)
    assert g.matched_band == "equity_max"


# --- AC3: incompatible combinations produce three resolution options ----------


def test_detect_conflict_produces_three_named_options():
    g = detect_conflict(0.10, -0.10)
    assert len(g.conflicts) == 1
    conflict = g.conflicts[0]
    assert conflict.field_a == "expected_annual_return"
    assert conflict.field_b == "max_tolerable_drawdown"
    assert {o.key for o in conflict.options} == {
        "keep_return",
        "keep_drawdown",
        "meet_in_middle",
    }


def test_detect_conflict_keep_return_uses_return_band():
    g = detect_conflict(0.10, -0.10)
    keep_return = next(o for o in g.conflicts[0].options if o.key == "keep_return")
    assert keep_return.implied_return_range[0] >= 0.085  # equity_max


def test_detect_conflict_keep_drawdown_uses_drawdown_band():
    g = detect_conflict(0.10, -0.10)
    keep_dd = next(o for o in g.conflicts[0].options if o.key == "keep_drawdown")
    assert keep_dd.implied_return_range[1] <= 0.04  # capital_preservation


def test_detect_conflict_meet_in_middle_uses_balanced_band():
    g = detect_conflict(0.10, -0.10)
    mid = next(o for o in g.conflicts[0].options if o.key == "meet_in_middle")
    assert mid.template_id == "balanced_long_term"


def test_detect_no_conflict_when_both_in_same_band():
    g = detect_conflict(0.05, -0.20)  # both balanced
    assert g.conflicts == []
    assert g.matched_band == "balanced"


# --- AC4: above-highest-band warning, no fabricated recommendation ------------


def test_anchor_above_highest_band_warns_and_recommends_nothing():
    g = anchor_on_return(0.25)
    assert g.matched_band == ABOVE_HIGHEST
    assert g.template_id == ""
    assert g.implied_return_range is None
    assert g.implied_drawdown_range is None
    assert g.scenarios == []
    assert len(g.warnings) == 1
    assert "above the highest configured band" in g.warnings[0]


def test_exact_highest_upper_bound_stays_in_band():
    g = anchor_on_return(0.11)
    assert g.matched_band == "equity_max"
    assert g.warnings == []


def test_detect_conflict_propagates_above_band_warning():
    g = detect_conflict(0.30, -0.10)
    assert g.matched_band == ABOVE_HIGHEST
    assert any("above the highest" in w for w in g.warnings)
    assert g.conflicts == []


# --- AC5: scenario translation -------------------------------------------------


def test_scenarios_without_portfolio_size_have_no_currency():
    g = anchor_on_return(0.10)
    assert g.scenarios != []
    assert all(s.currency_amount is None for s in g.scenarios)


def test_scenarios_with_portfolio_size_render_currency_amounts():
    g = anchor_on_return(0.10, portfolio_size=100_000_000, portfolio_currency="KRW")
    ep_2008 = next(s for s in g.scenarios if s.episode_year == 2008)
    assert ep_2008.currency_amount == pytest.approx(100_000_000 * -0.52)
    assert ep_2008.currency == "KRW"


def test_scenarios_cover_all_three_episodes():
    g = anchor_on_return(0.075)
    assert {s.episode_year for s in g.scenarios} == {2008, 2020, 2022}


def test_above_band_produces_no_scenarios():
    g = anchor_on_return(0.99)
    assert g.scenarios == []


# --- AC6: guidance adds no hard rejections; above-band leaves profile intact ---


def test_apply_guidance_yields_a_valid_profile():
    g = anchor_on_return(0.075)
    draft = apply_guidance_to_profile(DEFAULT_PROFILE, g)
    assert validate_profile(draft).is_valid
    assert draft.profile_id == DEFAULT_PROFILE.profile_id


def test_apply_guidance_above_band_returns_profile_unchanged():
    g = anchor_on_return(0.50)
    draft = apply_guidance_to_profile(DEFAULT_PROFILE, g)
    assert draft is DEFAULT_PROFILE


# --- Anchored value is preserved exactly (the stated number is never replaced) -


def test_return_anchor_preserves_exact_stated_return():
    # 0.08 sits inside growth (0.065–0.085); the saved return must stay 0.08,
    # not collapse to the band midpoint (0.075).
    g = anchor_on_return(0.08)
    draft = apply_guidance_to_profile(DEFAULT_PROFILE, g)
    assert draft.expected_annual_return == 0.08
    # the non-anchored side (drawdown) is derived from the band
    assert draft.max_tolerable_drawdown == pytest.approx(-0.375)


def test_drawdown_anchor_preserves_exact_stated_drawdown():
    g = anchor_on_drawdown(-0.22)  # inside balanced (-0.30–-0.15)
    draft = apply_guidance_to_profile(DEFAULT_PROFILE, g)
    assert draft.max_tolerable_drawdown == -0.22
    assert draft.expected_annual_return == pytest.approx(0.0525)  # balanced midpoint


def test_resolution_keep_return_preserves_stated_return():
    g = detect_conflict(0.10, -0.10)
    opt = next(o for o in g.conflicts[0].options if o.key == "keep_return")
    draft = apply_resolution_to_profile(
        DEFAULT_PROFILE, opt, stated_return=0.10, stated_drawdown=-0.10
    )
    assert draft.expected_annual_return == 0.10  # kept exactly
    assert draft.max_tolerable_drawdown == pytest.approx(-0.50)  # equity_max derived


def test_resolution_keep_drawdown_preserves_stated_drawdown():
    g = detect_conflict(0.10, -0.10)
    opt = next(o for o in g.conflicts[0].options if o.key == "keep_drawdown")
    draft = apply_resolution_to_profile(
        DEFAULT_PROFILE, opt, stated_return=0.10, stated_drawdown=-0.10
    )
    assert draft.max_tolerable_drawdown == -0.10  # kept exactly
    assert draft.expected_annual_return == pytest.approx(0.03)  # cap-pres derived


def test_resolution_meet_in_middle_derives_both_sides():
    g = detect_conflict(0.10, -0.10)
    opt = next(o for o in g.conflicts[0].options if o.key == "meet_in_middle")
    draft = apply_resolution_to_profile(
        DEFAULT_PROFILE, opt, stated_return=0.10, stated_drawdown=-0.10
    )
    assert draft.expected_annual_return == pytest.approx(0.0525)  # balanced
    assert draft.max_tolerable_drawdown == pytest.approx(-0.225)


# --- Derived guardrails (spec section 5) --------------------------------------


def test_each_band_carries_guardrails():
    for g in (anchor_on_return(0.03), anchor_on_return(0.05), anchor_on_return(0.075), anchor_on_return(0.10)):
        assert g.guardrails is not None
        assert g.guardrails.max_single_position_weight <= g.guardrails.max_sector_weight


def test_guardrails_scale_monotonically_with_risk():
    bands = [anchor_on_return(r).guardrails for r in (0.03, 0.05, 0.075, 0.10)]
    # cash buffer shrinks as risk rises; caps and turnover widen.
    assert [b.liquidity_buffer_months for b in bands] == sorted(
        (b.liquidity_buffer_months for b in bands), reverse=True
    )
    for attr in (
        "max_single_position_weight",
        "max_sector_weight",
        "max_monthly_turnover",
        "rebalance_band",
    ):
        values = [getattr(b, attr) for b in bands]
        assert values == sorted(values)


def test_apply_guidance_sets_guardrail_fields_from_band():
    g = anchor_on_return(0.03)  # capital_preservation, tightest caps
    draft = apply_guidance_to_profile(DEFAULT_PROFILE, g)
    assert draft.max_single_position_weight == 0.05
    assert draft.liquidity_buffer_months == 12.0
    assert draft.max_monthly_turnover == 0.05
    assert draft.rebalance_band == 0.03
    # personal / governance fields untouched
    assert draft.monthly_contribution == DEFAULT_PROFILE.monthly_contribution
    assert draft.trade_mode == DEFAULT_PROFILE.trade_mode
    assert validate_profile(draft).is_valid


# --- AC7: every number comes from the YAML (override changes the output) -------


def test_band_values_are_sourced_from_the_yaml_table():
    original_bands, original_episodes = gmod._BANDS, gmod._EPISODES
    gmod._BANDS = [
        RiskBand(
            name="capital_preservation",
            expected_return_range=(0.01, 0.02),
            typical_equity_weight=(0.0, 0.1),
            historical_drawdown_range=(-0.07, -0.03),
            min_recommended_horizon_years=1,
            template_id="capital_preservation",
            guardrails=_TEST_GUARDRAILS,
        )
    ]
    gmod._EPISODES = [
        HistoricalEpisode(2099, "Test", {"capital_preservation": -0.05})
    ]
    try:
        g = anchor_on_return(0.015)
        assert g.implied_return_range == pytest.approx((0.01, 0.02))
        assert g.min_recommended_horizon_years == 1
        assert g.scenarios[0].episode_year == 2099
    finally:
        gmod._BANDS = original_bands
        gmod._EPISODES = original_episodes


def test_every_band_template_id_exists_in_policy_templates():
    bad = [b.template_id for b in gmod._BANDS if b.template_id not in POLICY_TEMPLATES]
    assert not bad

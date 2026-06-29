from croesus.profiles.presets import band_by_name, list_presets, preset_profile
from croesus.profiles.seed_default_profile import DEFAULT_PROFILE


def test_list_presets_are_ascending_house_bands():
    names = [b.name for b in list_presets()]
    assert "capital_preservation" in names and "growth" in names
    # ascending risk → expected-return midpoints increase
    rets = [(b.expected_return_range[0] + b.expected_return_range[1]) / 2 for b in list_presets()]
    assert rets == sorted(rets)


def test_preset_profile_applies_band_and_preserves_identity():
    band = band_by_name("capital_preservation")
    profile, targets = preset_profile(band, DEFAULT_PROFILE)
    # guardrails copied from the band
    assert profile.liquidity_buffer_months == band.guardrails.liquidity_buffer_months
    assert profile.max_single_position_weight == band.guardrails.max_single_position_weight
    assert profile.investment_horizon_years == band.min_recommended_horizon_years
    # drawdown is negative (a loss), taken from the band midpoint
    assert profile.max_tolerable_drawdown < 0
    # identity preserved so a preset edits the active profile, not replaces it
    assert profile.profile_id == DEFAULT_PROFILE.profile_id
    assert profile.base_currency == DEFAULT_PROFILE.base_currency
    # targets are attributed to the same profile and non-empty
    assert targets and all(t.profile_id == DEFAULT_PROFILE.profile_id for t in targets)


def test_band_by_name_unknown_returns_none():
    assert band_by_name("does_not_exist") is None

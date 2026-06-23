from __future__ import annotations
from dataclasses import replace

from croesus.profiles.models import InvestorProfile, PolicyTarget, Currency, TradeMode
from croesus.profiles.validation import validate_profile, validate_policy_targets

_FLOAT_FIELDS = [
    "expected_annual_return", "max_tolerable_drawdown", "monthly_contribution",
    "liquidity_buffer_months", "max_single_position_weight", "max_sector_weight",
    "max_industry_weight", "max_theme_weight", "max_country_weight",
    "max_currency_weight", "max_monthly_turnover", "rebalance_band",
]


def _as_list(value):
    return value if isinstance(value, list) else [value]


def parse_profile_form(form: dict, existing: InvestorProfile):
    errors: list[str] = []
    kwargs: dict = {}
    for key in _FLOAT_FIELDS:
        try:
            kwargs[key] = float(form.get(key, ""))
        except (TypeError, ValueError):
            errors.append(f"{key}: 숫자를 입력하세요")
    try:
        kwargs["investment_horizon_years"] = int(form.get("investment_horizon_years", ""))
    except (TypeError, ValueError):
        errors.append("investment_horizon_years: 정수를 입력하세요")
    try:
        kwargs["trade_mode"] = TradeMode(form.get("trade_mode", existing.trade_mode.value))
    except ValueError:
        errors.append("trade_mode: 허용되지 않는 값")

    if errors:
        return existing, [], errors

    profile = replace(existing, **kwargs)

    names = _as_list(form.get("sleeve_name", []))
    tw = _as_list(form.get("target_weight", []))
    mn = _as_list(form.get("min_weight", []))
    mx = _as_list(form.get("max_weight", []))
    targets: list[PolicyTarget] = []
    for i, name in enumerate(names):
        if not name:
            continue
        try:
            target_weight = float(tw[i])
        except (IndexError, ValueError):
            errors.append(f"{name}: 타깃 비중이 숫자가 아닙니다")
            continue
        min_w = float(mn[i]) if i < len(mn) and mn[i] not in ("", None) else None
        max_w = float(mx[i]) if i < len(mx) and mx[i] not in ("", None) else None
        targets.append(PolicyTarget(profile_id=profile.profile_id, sleeve_name=name,
            target_weight=target_weight, min_weight=min_w, max_weight=max_w, metadata={}))

    pr = validate_profile(profile)
    tr = validate_policy_targets(targets)
    errors += [str(e) for e in getattr(pr, "errors", [])]
    errors += [str(e) for e in getattr(tr, "errors", [])]
    return profile, targets, errors

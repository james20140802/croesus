from __future__ import annotations

from datetime import date
from typing import Any

from croesus.portfolio.actions import ProposedAction
from croesus.portfolio.models import AssetAttrs, Exposure, Holding, PolicyDrift
from croesus.profiles.models import InvestorProfile
from croesus.profiles.validation import validate_profile
from croesus.screening.models import ScreeningCandidate

_EXPOSURE_REASON_CODES = {
    "sector": "SECTOR_OVER_MAX",
    "industry": "INDUSTRY_OVER_MAX",
    "theme": "THEME_OVER_MAX",
    "country": "COUNTRY_OVER_MAX",
    "currency": "CURRENCY_OVER_MAX",
}

_ACTION_PRIORITY = {
    "hold": 1,
    "raise_cash": 2,
    "trim": 3,
    "rebalance_to_band": 4,
    "block_new_buy": 5,
    "watch": 6,
    "add": 7,
}


def generate_proposed_actions(
    run_id: str,
    *,
    portfolio_id: str,
    as_of_date: date,
    profile: InvestorProfile,
    total_market_value: float,
    exposures: list[Exposure] | None = None,
    drifts: list[PolicyDrift] | None = None,
    holdings: list[Holding] | None = None,
    assets_by_id: dict[str, AssetAttrs] | None = None,
    screening_candidates: list[ScreeningCandidate] | None = None,
    macro_state: Any | None = None,
) -> list[ProposedAction]:
    """Generate deterministic Level 1 proposal actions.

    This function consumes already-computed portfolio state. It does not fetch
    prices, prepare broker orders, or perform qualitative research.
    """
    validation = validate_profile(profile)
    if not validation.is_valid:
        return [
            _action(
                run_id,
                1,
                action_type="hold",
                reason_codes=["PROFILE_INVALID"],
                reason=(
                    "Portfolio action generation is blocked until the investor "
                    "profile is valid."
                ),
            )
        ]

    exposure_rows = exposures or []
    drift_rows = drifts or []
    holding_rows = holdings or []
    asset_attrs = assets_by_id or {}
    candidates = screening_candidates or []
    actions: list[ProposedAction] = []
    counter = 1

    for exposure in exposure_rows:
        if exposure.exposure_type != "position" or not exposure.is_violation:
            continue
        limit = _limit(exposure, profile.max_single_position_weight)
        if limit is None:
            continue
        actions.append(
            _action(
                run_id,
                counter,
                asset_id=exposure.exposure_name,
                action_type="trim",
                current_weight=exposure.weight,
                target_weight=limit,
                proposed_weight=limit,
                estimated_trade_value=_trade_value(
                    max(exposure.weight - limit, 0.0), total_market_value
                ),
                reason_codes=["POSITION_OVER_MAX"],
                reason=(
                    f"Trim {exposure.exposure_name} from "
                    f"{_pct(exposure.weight)} to {_pct(limit)}."
                ),
            )
        )
        counter += 1

    overexposed: list[Exposure] = []
    for exposure in exposure_rows:
        if exposure.exposure_type not in _EXPOSURE_REASON_CODES or not exposure.is_violation:
            continue
        reason_code = _EXPOSURE_REASON_CODES[exposure.exposure_type]
        actions.append(
            _action(
                run_id,
                counter,
                sleeve_name=exposure.exposure_name,
                action_type="block_new_buy",
                current_weight=exposure.weight,
                target_weight=exposure.limit_weight,
                proposed_weight=exposure.limit_weight,
                estimated_trade_value=0.0,
                reason_codes=[reason_code],
                reason=(
                    f"Block new buys in {exposure.exposure_name}; "
                    f"{exposure.exposure_type} exposure is {_pct(exposure.weight)}."
                ),
            )
        )
        counter += 1
        overexposed.append(exposure)

        limit = _limit(exposure, None)
        if limit is None:
            continue
        if exposure.weight > limit + profile.rebalance_band:
            trim = _largest_holding_in_exposure(exposure, holding_rows, asset_attrs)
            if trim is None:
                continue
            asset_id, holding_weight = trim
            excess_value = max(exposure.weight - limit, 0.0) * total_market_value
            proposed_weight = max(holding_weight - (excess_value / total_market_value), 0.0)
            actions.append(
                _action(
                    run_id,
                    counter,
                    asset_id=asset_id,
                    action_type="trim",
                    current_weight=holding_weight,
                    target_weight=proposed_weight,
                    proposed_weight=proposed_weight,
                    estimated_trade_value=min(
                        excess_value, holding_weight * total_market_value
                    ),
                    reason_codes=[reason_code],
                    reason=(
                        f"Trim {asset_id}, the largest holding in overexposed "
                        f"{exposure.exposure_name}."
                    ),
                )
            )
            counter += 1

    cash_needs_restoration = False
    for drift in drift_rows:
        if not drift.is_outside_band:
            continue
        if _is_cash_sleeve(drift) and drift.min_weight is not None:
            if drift.current_weight < drift.min_weight:
                cash_needs_restoration = True
                proposed = drift.target_weight or drift.min_weight
                actions.append(
                    _action(
                        run_id,
                        counter,
                        sleeve_name=drift.sleeve_name,
                        action_type="raise_cash",
                        current_weight=drift.current_weight,
                        target_weight=drift.target_weight,
                    proposed_weight=proposed,
                    estimated_trade_value=_trade_value(
                        max(proposed - drift.current_weight, 0.0), total_market_value
                    ),
                        reason_codes=["CASH_BELOW_BUFFER"],
                        reason=(
                            f"Raise cash from {_pct(drift.current_weight)} "
                            f"toward {_pct(proposed)}."
                        ),
                    )
                )
                counter += 1
            continue

        if drift.max_weight is not None and drift.current_weight > drift.max_weight:
            proposed = min(drift.target_weight, drift.max_weight)
            actions.append(
                _action(
                    run_id,
                    counter,
                    sleeve_name=drift.sleeve_name,
                    action_type="rebalance_to_band",
                    current_weight=drift.current_weight,
                    target_weight=drift.target_weight,
                    proposed_weight=proposed,
                    estimated_trade_value=_trade_value(
                        max(drift.current_weight - proposed, 0.0), total_market_value
                    ),
                    reason_codes=["SLEEVE_OVER_BAND"],
                    reason=f"Reduce {drift.sleeve_name} toward policy target.",
                )
            )
            counter += 1
        elif drift.min_weight is not None and drift.current_weight < drift.min_weight:
            proposed = max(drift.target_weight, drift.min_weight)
            actions.append(
                _action(
                    run_id,
                    counter,
                    sleeve_name=drift.sleeve_name,
                    action_type="rebalance_to_band",
                    current_weight=drift.current_weight,
                    target_weight=drift.target_weight,
                    proposed_weight=proposed,
                    estimated_trade_value=_trade_value(
                        max(proposed - drift.current_weight, 0.0), total_market_value
                    ),
                    reason_codes=["SLEEVE_UNDER_BAND"],
                    reason=f"Add to {drift.sleeve_name} toward policy target.",
                )
            )
            counter += 1

    positioning = getattr(macro_state, "positioning", None)
    if positioning == "Defensive":
        actions = [
            _with_reason(action, "MACRO_DEFENSIVE_REDUCE_CONCENTRATION")
            if action.action_type in {"trim", "raise_cash"}
            else action
            for action in actions
        ]

    if not cash_needs_restoration:
        for candidate in candidates:
            if candidate.decision_bucket != "candidate":
                actions.append(
                    _watch_action(
                        run_id,
                        counter,
                        candidate,
                        ["QUALITATIVE_RESEARCH_REQUIRED"],
                        "Keep candidate on watchlist because it is blocked by portfolio fit.",
                    )
                )
                counter += 1
                continue

            sleeve_name = str(candidate.metadata.get("sleeve_name") or "satellite_equity")
            if _candidate_matches_overexposure(candidate, overexposed, asset_attrs):
                actions.append(
                    _watch_action(
                        run_id,
                        counter,
                        candidate,
                        ["QUALITATIVE_RESEARCH_REQUIRED"],
                        "Candidate is attractive but blocked by current exposure.",
                    )
                )
                counter += 1
                continue

            if candidate.metadata.get("requires_research"):
                actions.append(
                    _watch_action(
                        run_id,
                        counter,
                        candidate,
                        ["QUALITATIVE_RESEARCH_REQUIRED"],
                        "Candidate requires qualitative research before action.",
                    )
                )
                counter += 1
                continue

            if positioning == "Defensive":
                actions.append(
                    _watch_action(
                        run_id,
                        counter,
                        candidate,
                        ["MACRO_DEFENSIVE_REDUCE_CONCENTRATION"],
                        "Defensive macro posture blocks new risk additions.",
                    )
                )
                counter += 1
                continue

            if positioning == "Cautious" and "satellite" in sleeve_name.lower():
                actions.append(
                    _watch_action(
                        run_id,
                        counter,
                        candidate,
                        ["MACRO_CAUTIOUS_TIGHTEN_RISK"],
                        "Cautious macro posture blocks new satellite additions.",
                    )
                )
                counter += 1
                continue

            drift = _drift_for_sleeve(drift_rows, sleeve_name)
            add_weight = _candidate_add_weight(drift)
            if add_weight <= 0:
                actions.append(
                    _watch_action(
                        run_id,
                        counter,
                        candidate,
                        ["NO_ACTION_WITHIN_POLICY"],
                        "Candidate is attractive but policy sleeve does not need an add.",
                    )
                )
                counter += 1
                continue

            actions.append(
                _action(
                    run_id,
                    counter,
                    asset_id=candidate.asset_id,
                    sleeve_name=sleeve_name,
                    action_type="add",
                    current_weight=drift.current_weight if drift else None,
                    target_weight=drift.target_weight if drift else None,
                    proposed_weight=(
                        (drift.current_weight if drift else 0.0) + add_weight
                    ),
                    estimated_trade_value=_trade_value(add_weight, total_market_value),
                    reason_codes=["FACTOR_SCORE_SUPPORTS_ADD"],
                    reason=f"Add {candidate.asset_id}; screening score supports the sleeve.",
                )
            )
            counter += 1

    actions = _apply_turnover_limit(actions, profile.max_monthly_turnover, total_market_value)
    if not actions:
        actions = [
            _action(
                run_id,
                1,
                action_type="hold",
                reason_codes=["NO_ACTION_WITHIN_POLICY"],
                reason="No action is needed; portfolio is inside current policy constraints.",
            )
        ]
    return actions


def _action(
    run_id: str,
    index: int,
    *,
    action_type: str,
    reason_codes: list[str],
    reason: str,
    asset_id: str | None = None,
    sleeve_name: str | None = None,
    current_weight: float | None = None,
    target_weight: float | None = None,
    proposed_weight: float | None = None,
    estimated_trade_value: float | None = None,
    requires_research: bool = False,
) -> ProposedAction:
    return ProposedAction(
        action_id=f"{run_id}-{index:03d}",
        run_id=run_id,
        asset_id=asset_id,
        sleeve_name=sleeve_name,
        action_type=action_type,
        current_weight=current_weight,
        target_weight=target_weight,
        proposed_weight=proposed_weight,
        estimated_trade_value=estimated_trade_value,
        reason_codes=reason_codes,
        human_readable_reason=reason,
        requires_research=requires_research,
        requires_user_approval=action_type not in {"hold", "watch", "block_new_buy"},
    )


def _watch_action(
    run_id: str,
    index: int,
    candidate: ScreeningCandidate,
    reason_codes: list[str],
    reason: str,
) -> ProposedAction:
    return _action(
        run_id,
        index,
        asset_id=candidate.asset_id,
        sleeve_name=candidate.metadata.get("sleeve_name"),
        action_type="watch",
        reason_codes=reason_codes,
        reason=reason,
        requires_research="QUALITATIVE_RESEARCH_REQUIRED" in reason_codes,
    )


def _with_reason(action: ProposedAction, reason_code: str) -> ProposedAction:
    if reason_code in action.reason_codes:
        return action
    return ProposedAction(
        action_id=action.action_id,
        run_id=action.run_id,
        asset_id=action.asset_id,
        sleeve_name=action.sleeve_name,
        action_type=action.action_type,
        current_weight=action.current_weight,
        target_weight=action.target_weight,
        proposed_weight=action.proposed_weight,
        estimated_trade_value=action.estimated_trade_value,
        reason_codes=action.reason_codes + [reason_code],
        human_readable_reason=action.human_readable_reason,
        requires_research=action.requires_research,
        requires_user_approval=action.requires_user_approval,
    )


def _with_trade_value_and_reason(
    action: ProposedAction, trade_value: float, reason_code: str
) -> ProposedAction:
    return ProposedAction(
        action_id=action.action_id,
        run_id=action.run_id,
        asset_id=action.asset_id,
        sleeve_name=action.sleeve_name,
        action_type=action.action_type,
        current_weight=action.current_weight,
        target_weight=action.target_weight,
        proposed_weight=action.proposed_weight,
        estimated_trade_value=trade_value,
        reason_codes=action.reason_codes
        if reason_code in action.reason_codes
        else action.reason_codes + [reason_code],
        human_readable_reason=action.human_readable_reason,
        requires_research=action.requires_research,
        requires_user_approval=action.requires_user_approval,
    )


def _limit(exposure: Exposure, fallback: float | None) -> float | None:
    return exposure.limit_weight if exposure.limit_weight is not None else fallback


def _is_cash_sleeve(drift: PolicyDrift) -> bool:
    return drift.sleeve_name.lower() == "cash"


def _drift_for_sleeve(
    drifts: list[PolicyDrift], sleeve_name: str
) -> PolicyDrift | None:
    for drift in drifts:
        if drift.sleeve_name == sleeve_name:
            return drift
    return None


def _candidate_add_weight(drift: PolicyDrift | None) -> float:
    if drift is None:
        return 0.0
    if drift.current_weight < drift.target_weight:
        if drift.max_weight is not None and drift.current_weight >= drift.max_weight:
            return 0.0
        return drift.target_weight - drift.current_weight
    if drift.max_weight is not None and drift.current_weight < drift.max_weight:
        return min(drift.max_weight - drift.current_weight, drift.target_weight)
    return 0.0


def _largest_holding_in_exposure(
    exposure: Exposure,
    holdings: list[Holding],
    assets_by_id: dict[str, AssetAttrs],
) -> tuple[str, float] | None:
    matching: list[tuple[str, float]] = []
    total = sum(h.market_value or 0.0 for h in holdings)
    if total <= 0:
        return None
    for holding in holdings:
        if _holding_matches_exposure(holding, exposure, assets_by_id):
            matching.append((holding.asset_id, (holding.market_value or 0.0) / total))
    if not matching:
        return None
    return max(matching, key=lambda item: item[1])


def _candidate_matches_overexposure(
    candidate: ScreeningCandidate,
    overexposed: list[Exposure],
    assets_by_id: dict[str, AssetAttrs],
) -> bool:
    holding = Holding(
        portfolio_id="candidate",
        asset_id=candidate.asset_id,
        as_of_date=date.today(),
        quantity=0.0,
        market_value=0.0,
        currency="USD",
    )
    return any(
        _holding_matches_exposure(holding, exposure, assets_by_id)
        for exposure in overexposed
    )


def _holding_matches_exposure(
    holding: Holding,
    exposure: Exposure,
    assets_by_id: dict[str, AssetAttrs],
) -> bool:
    attrs = assets_by_id.get(holding.asset_id, AssetAttrs(currency=holding.currency))
    if exposure.exposure_type == "sector":
        return attrs.sector == exposure.exposure_name
    if exposure.exposure_type == "industry":
        return attrs.industry == exposure.exposure_name
    if exposure.exposure_type == "theme":
        return exposure.exposure_name in attrs.theme_tags
    if exposure.exposure_type == "country":
        return attrs.country == exposure.exposure_name
    if exposure.exposure_type == "currency":
        return (attrs.currency or holding.currency) == exposure.exposure_name
    return False


def _apply_turnover_limit(
    actions: list[ProposedAction],
    max_monthly_turnover: float,
    total_market_value: float,
) -> list[ProposedAction]:
    if total_market_value <= 0:
        return actions
    limit = max_monthly_turnover * total_market_value
    if sum(action.estimated_trade_value or 0.0 for action in actions) <= limit:
        return actions

    remaining = limit
    kept: list[ProposedAction] = []
    for action in sorted(actions, key=lambda a: (_ACTION_PRIORITY[a.action_type], a.action_id)):
        trade_value = action.estimated_trade_value or 0.0
        if trade_value <= 0:
            kept.append(action)
            continue
        if trade_value <= remaining:
            kept.append(action)
            remaining -= trade_value
            continue
        if action.action_type == "add":
            continue
        if remaining > 0:
            kept.append(_with_trade_value_and_reason(action, remaining, "TURNOVER_LIMIT"))
            remaining = 0.0
        else:
            kept.append(_with_trade_value_and_reason(action, 0.0, "TURNOVER_LIMIT"))
    return kept


def _pct(value: float) -> str:
    return f"{value:.1%}"


def _trade_value(weight_delta: float, total_market_value: float) -> float:
    return round(weight_delta * total_market_value, 2)

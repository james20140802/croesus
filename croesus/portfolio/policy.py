from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from croesus.portfolio.models import AssetAttrs, Holding, PolicyDrift, is_cash
from croesus.profiles.models import PolicyTarget

_DEFAULT_FALLBACK_SLEEVE = "satellite_equity"

# Sleeve-match specificity: an explicit asset id beats a theme tag, which beats
# a broad asset-type bucket. Higher wins when a holding matches several sleeves.
_PRIORITY_ASSET_ID = 3
_PRIORITY_THEME = 2
_PRIORITY_ASSET_TYPE = 1


@dataclass(frozen=True)
class PolicyDriftResult:
    drifts: list[PolicyDrift]
    warnings: list[str]


def compute_policy_drifts(
    holdings: list[Holding],
    assets_by_id: dict[str, AssetAttrs],
    targets: list[PolicyTarget],
    *,
    portfolio_id: str,
    as_of_date: date,
    fallback_sleeve: str = _DEFAULT_FALLBACK_SLEEVE,
) -> PolicyDriftResult:
    """Map holdings to policy sleeves and measure drift from target weights.

    Sleeve membership is read from each target's ``metadata`` (``asset_ids``,
    ``theme_tags``, ``asset_types``). A holding that matches no sleeve is routed
    to ``fallback_sleeve`` (default ``satellite_equity``) and a warning is
    emitted. One :class:`PolicyDrift` row is produced per defined sleeve, even
    sleeves that hold nothing, where ``drift = current_weight - target_weight``
    and ``is_outside_band`` is true when the current weight leaves the
    ``[min_weight, max_weight]`` band.
    """
    total = sum(_market_value(h) for h in holdings)
    sleeve_mv: dict[str, float] = defaultdict(float)
    warnings: list[str] = []

    for h in holdings:
        attrs = assets_by_id.get(h.asset_id)
        sleeve = _match_sleeve(h, attrs, targets)
        if sleeve is None:
            sleeve = fallback_sleeve
            warnings.append(
                f"holding {h.asset_id} did not match any policy sleeve; "
                f"classified as {fallback_sleeve}"
            )
        sleeve_mv[sleeve] += _market_value(h)

    drifts: list[PolicyDrift] = []
    for target in sorted(targets, key=lambda t: t.sleeve_name):
        current = (sleeve_mv.get(target.sleeve_name, 0.0) / total) if total > 0 else 0.0
        outside = (
            (target.min_weight is not None and current < target.min_weight)
            or (target.max_weight is not None and current > target.max_weight)
        )
        drifts.append(
            PolicyDrift(
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
                sleeve_name=target.sleeve_name,
                current_weight=current,
                target_weight=target.target_weight,
                min_weight=target.min_weight,
                max_weight=target.max_weight,
                drift=current - target.target_weight,
                is_outside_band=outside,
            )
        )

    return PolicyDriftResult(drifts=drifts, warnings=warnings)


def _match_sleeve(
    holding: Holding,
    attrs: AssetAttrs | None,
    targets: list[PolicyTarget],
) -> str | None:
    """Return the best-matching sleeve name for a holding, or None.

    Criteria are OR-combined within a sleeve; across sleeves the most specific
    match wins (asset id > theme tag > asset type). Ties break by sleeve name
    for determinism.
    """
    best_priority = 0
    best_sleeve: str | None = None
    theme_tags = attrs.theme_tags if attrs else []
    asset_type = attrs.asset_type if attrs else None

    for target in sorted(targets, key=lambda t: t.sleeve_name):
        md = target.metadata or {}
        if is_cash(holding.asset_id) and _target_accepts_cash(target):
            priority = _PRIORITY_ASSET_ID
        elif holding.asset_id in (md.get("asset_ids") or []):
            priority = _PRIORITY_ASSET_ID
        elif theme_tags and any(t in (md.get("theme_tags") or []) for t in theme_tags):
            priority = _PRIORITY_THEME
        elif asset_type is not None and asset_type in (md.get("asset_types") or []):
            priority = _PRIORITY_ASSET_TYPE
        else:
            continue
        if priority > best_priority:
            best_priority = priority
            best_sleeve = target.sleeve_name

    return best_sleeve


def _target_accepts_cash(target: PolicyTarget) -> bool:
    md = target.metadata or {}
    return target.sleeve_name == "cash" or "cash" in (md.get("asset_types") or [])


def _market_value(holding: Holding) -> float:
    return holding.market_value or 0.0

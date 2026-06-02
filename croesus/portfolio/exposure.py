from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from croesus.portfolio.models import AssetAttrs, Exposure, Holding, is_cash


@dataclass(frozen=True)
class ExposureLimits:
    """Concentration caps drawn from the active profile.

    Each limit is optional; a ``None`` cap means the corresponding exposure
    dimension is never flagged as a violation.
    """

    max_single_position_weight: float | None = None
    max_sector_weight: float | None = None
    max_industry_weight: float | None = None
    max_theme_weight: float | None = None
    max_country_weight: float | None = None
    max_currency_weight: float | None = None


def compute_exposures(
    holdings: list[Holding],
    assets_by_id: dict[str, AssetAttrs],
    limits: ExposureLimits,
    *,
    portfolio_id: str,
    as_of_date: date,
    base_currency: str = "USD",
    base_country: str = "US",
) -> list[Exposure]:
    """Aggregate holdings into position/sector/industry/theme/country/currency rows.

    Position weight is ``market_value / total_market_value``. Cash asset ids
    (``CASH_<CUR>``) are classified as sector/industry ``Cash``. Holdings with
    no ``theme_tags`` contribute to no theme exposure. Each row is flagged
    ``is_violation`` when its weight exceeds the matching cap in ``limits``.
    """
    total = sum(_market_value(h) for h in holdings)
    if total <= 0:
        return []

    def attrs_for(holding: Holding) -> AssetAttrs:
        if is_cash(holding.asset_id):
            return AssetAttrs(
                asset_type="cash",
                sector="Cash",
                industry="Cash",
                country=base_country,
                currency=holding.currency or base_currency,
                theme_tags=[],
            )
        found = assets_by_id.get(holding.asset_id)
        if found is None:
            return AssetAttrs(currency=holding.currency)
        return found

    exposures: list[Exposure] = []

    # position: one row per asset (summed if an asset appears more than once)
    position_mv: dict[str, float] = defaultdict(float)
    for h in holdings:
        position_mv[h.asset_id] += _market_value(h)
    for asset_id in sorted(position_mv):
        exposures.append(
            _exposure(
                portfolio_id, as_of_date, "position", asset_id,
                position_mv[asset_id], total, limits.max_single_position_weight,
            )
        )

    # categorical single-value dimensions
    dimensions = (
        ("sector", lambda a: a.sector or "Unknown", limits.max_sector_weight),
        ("industry", lambda a: a.industry or "Unknown", limits.max_industry_weight),
        ("country", lambda a: a.country or "Unknown", limits.max_country_weight),
        ("currency", lambda a: a.currency or "Unknown", limits.max_currency_weight),
    )
    for exposure_type, key_of, cap in dimensions:
        bucket: dict[str, float] = defaultdict(float)
        for h in holdings:
            bucket[key_of(attrs_for(h))] += _market_value(h)
        for name in sorted(bucket):
            exposures.append(
                _exposure(
                    portfolio_id, as_of_date, exposure_type, name,
                    bucket[name], total, cap,
                )
            )

    # theme: a holding contributes its full value to each of its tags; untagged
    # holdings are skipped entirely (theme weights need not sum to 1.0).
    theme_mv: dict[str, float] = defaultdict(float)
    for h in holdings:
        for tag in attrs_for(h).theme_tags:
            theme_mv[tag] += _market_value(h)
    for tag in sorted(theme_mv):
        exposures.append(
            _exposure(
                portfolio_id, as_of_date, "theme", tag,
                theme_mv[tag], total, limits.max_theme_weight,
            )
        )

    return exposures


def _exposure(
    portfolio_id: str,
    as_of_date: date,
    exposure_type: str,
    exposure_name: str,
    market_value: float,
    total: float,
    cap: float | None,
) -> Exposure:
    weight = market_value / total
    is_violation = cap is not None and weight > cap
    return Exposure(
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        exposure_type=exposure_type,
        exposure_name=exposure_name,
        weight=weight,
        market_value=market_value,
        limit_weight=cap,
        is_violation=is_violation,
    )


def _market_value(holding: Holding) -> float:
    return holding.market_value or 0.0

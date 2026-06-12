"""
Performance and goal tracking core (Sprint 006d).

This module is the pure, DB-free core that turns a profile's *target* return
into a measurable progress report. Given the portfolio value at the start and
end of a period, the external cash flows in between, and the profile's
``expected_annual_return``, it answers one question:

    Am I on track for the return I said I wanted, and at what risk?

It is deliberately honest about two things a naive "value went up" number gets
wrong:

  - **Deposits are not investment gain.** Adding $1,000 of new cash is not a
    return. :func:`compute_investment_return` subtracts net external
    contributions before computing a return.
  - **Return without risk is misleading.** Every period carries a
    :data:`risk_status` derived from concentration violations, policy drift, and
    trailing drawdown, so "ahead of goal while over budget" cannot hide.

Target returns are **goals, not guarantees** — every rendered surface says so.

Annualization, classification, and attribution are simple first-pass
approximations (see the spec's "approximate and explicit about limitations").
Time-/money-weighted returns and richer attribution can replace the internals
later without changing the result shape a dashboard consumes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# ── Periods (stable product contract — a dashboard keys cards off these) ──────
PERIOD_1M = "1m"
PERIOD_3M = "3m"
PERIOD_6M = "6m"
PERIOD_1Y = "1y"
PERIOD_SINCE_INCEPTION = "since_inception"

DEFAULT_PERIODS: tuple[str, ...] = (
    PERIOD_1M,
    PERIOD_3M,
    PERIOD_6M,
    PERIOD_1Y,
    PERIOD_SINCE_INCEPTION,
)

# How many whole months back each finite period starts. since_inception has no
# fixed lookback — it starts before the first dollar (start_value == 0).
_PERIOD_MONTHS: dict[str, int] = {
    PERIOD_1M: 1,
    PERIOD_3M: 3,
    PERIOD_6M: 6,
    PERIOD_1Y: 12,
}

# ── Goal status (compared against the profile's expected_annual_return) ───────
GOAL_AHEAD = "ahead_of_goal"
GOAL_NEAR = "near_goal"
GOAL_BEHIND = "behind_goal"
GOAL_INSUFFICIENT = "insufficient_history"

# ── Risk status (shown beside the return so risk cannot hide behind a gain) ───
RISK_WITHIN = "within_budget"
RISK_WATCH = "watch"
RISK_OVER = "over_budget"
RISK_UNKNOWN = "unknown"

# A return within this many percentage points of target counts as "near goal"
# rather than ahead/behind, so small noise is not reported as a verdict.
NEAR_GOAL_BAND = 0.02

# Annualizing a return from a window shorter than this is too noisy to compare
# against an annual target; such periods are reported as insufficient history.
MIN_DAYS_FOR_ANNUALIZATION = 20

_DAYS_PER_YEAR = 365.0


@dataclass(frozen=True)
class Attribution:
    """Lightweight, approximate decomposition of a period's value change.

    The buckets sum to the *total* value change of the period::

        end_value - start_value
          = net_contributions          (external cash in/out)
          + market_movement            (unrealized mark change — the residual)
          + realized                   (realized P&L from sells)
          + dividends                  (dividend income)

    ``market_movement`` is a residual, so any reconciliation gap between the
    snapshot values and the transaction-derived figures lands there. This is a
    first-pass attribution, not benchmark- or factor-relative.
    """

    net_contributions: float
    realized: float
    dividends: float
    market_movement: float | None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PerformancePeriod:
    """One ``(portfolio, as_of_date, period)`` progress row.

    Mirrors a row of ``portfolio_performance_snapshots``. ``investment_return_pct``
    is the raw period return; ``annualized_return_pct`` is what the goal status
    and ``return_gap_pct`` are computed against (and what a dashboard shows next
    to an annual target).
    """

    portfolio_id: str
    as_of_date: date
    period: str
    start_value: float | None
    end_value: float | None
    net_contributions: float
    investment_return: float | None
    investment_return_pct: float | None
    annualized_return_pct: float | None
    target_return_pct: float | None
    return_gap_pct: float | None
    max_drawdown_pct: float | None
    risk_status: str
    status: str
    attribution: Attribution
    risk_reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PerformanceCheckResult:
    """App-ready result: structured rows for dashboard cards and reports."""

    portfolio_id: str
    as_of_date: date
    periods: list[PerformancePeriod]
    warnings: list[str] = field(default_factory=list)

    # Every surface that shows a target must say it is a goal, not a guarantee.
    disclaimer: str = (
        "Target returns are goals, not guarantees. Figures are progress "
        "estimates from available history, not a promise of future results."
    )


def minus_months(d: date, months: int) -> date:
    """Return ``d`` shifted back ``months`` whole months, clamping the day.

    Pure date arithmetic (no dateutil dependency). Day overflow is clamped to
    the last valid day of the target month, e.g. ``minus_months(Mar 31, 1)`` is
    ``Feb 28`` (or 29 in a leap year).
    """
    month_index = (d.year * 12 + (d.month - 1)) - months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    last_day = _days_in_month(year, month)
    return date(year, month, min(d.day, last_day))


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - date(year, month, 1)).days


def period_start_date(as_of_date: date, period: str) -> date | None:
    """Window start for ``period``. ``None`` means "since inception" (no bound)."""
    months = _PERIOD_MONTHS.get(period)
    if months is None:
        return None
    return minus_months(as_of_date, months)


def compute_investment_return(
    start_value: float,
    end_value: float,
    net_contributions: float,
) -> tuple[float, float, float | None]:
    """Contribution-adjusted return for a period.

    Returns ``(investment_return, adjusted_start_value, investment_return_pct)``.

    ``investment_return = end_value - start_value - net_contributions`` strips
    external deposits/withdrawals so new cash is never counted as a gain. The
    return percent is taken over ``adjusted_start_value = start_value +
    net_contributions`` (the capital actually at work over the window), which
    also yields a meaningful denominator for ``since_inception`` where
    ``start_value`` is zero. ``investment_return_pct`` is ``None`` when the
    adjusted base is not positive (nothing meaningful to divide by).
    """
    investment_return = end_value - start_value - net_contributions
    adjusted_start_value = start_value + net_contributions
    if adjusted_start_value <= 0:
        return investment_return, adjusted_start_value, None
    return investment_return, adjusted_start_value, investment_return / adjusted_start_value


def annualize_return(period_pct: float | None, days: int) -> float | None:
    """Annualize a raw period return measured over ``days``.

    ``(1 + r) ** (365 / days) - 1``. Returns ``None`` for windows shorter than
    :data:`MIN_DAYS_FOR_ANNUALIZATION` (extrapolation too noisy to compare to an
    annual target) or when ``period_pct`` is ``None``. A total loss
    (``period_pct <= -1``) annualizes to ``-100%``.
    """
    if period_pct is None or days < MIN_DAYS_FOR_ANNUALIZATION:
        return None
    growth = 1.0 + period_pct
    if growth <= 0:
        return -1.0
    return growth ** (_DAYS_PER_YEAR / days) - 1.0


def classify_goal(
    annualized_pct: float | None,
    target_pct: float | None,
    *,
    band: float = NEAR_GOAL_BAND,
) -> str:
    """Goal status from the annualized return vs. the annual target.

    ``insufficient_history`` when either input is missing (history too short to
    annualize, or no target on the profile) — never a misleading verdict.
    """
    if annualized_pct is None or target_pct is None:
        return GOAL_INSUFFICIENT
    gap = annualized_pct - target_pct
    if gap > band:
        return GOAL_AHEAD
    if gap < -band:
        return GOAL_BEHIND
    return GOAL_NEAR


def assess_risk(
    *,
    n_violations: int,
    n_drift_outside: int,
    max_drawdown_pct: float | None,
    max_tolerable_drawdown: float | None,
    has_data: bool,
) -> tuple[str, list[str]]:
    """Risk status beside the return, with the drivers behind the verdict.

    ``over_budget`` on any hard concentration violation or a drawdown at/over
    the profile's tolerance; ``watch`` on policy drift outside band or a
    drawdown within 80% of tolerance; ``within_budget`` otherwise.
    ``unknown`` when there is no snapshot to assess.

    The reasons name what triggered the status — a concentration-driven
    ``over_budget`` next to a 0.0% drawdown reads as a contradiction unless
    every surface can say why.
    """
    if not has_data:
        return RISK_UNKNOWN, ["no snapshot to assess"]

    # ``is not None`` (not truthiness): a 0.0 tolerance means "any drawdown is a
    # breach", which is the opposite of "no limit set" (None).
    has_limit = max_tolerable_drawdown is not None
    measurable = max_drawdown_pct is not None and has_limit

    reasons: list[str] = []
    if n_violations > 0:
        plural = "s" if n_violations != 1 else ""
        reasons.append(f"{n_violations} concentration violation{plural}")
    if measurable and max_drawdown_pct >= max_tolerable_drawdown:
        reasons.append(
            f"drawdown {max_drawdown_pct:.1%} >= limit {max_tolerable_drawdown:.1%}"
        )
    if reasons:
        return RISK_OVER, reasons

    if n_drift_outside > 0:
        plural = "s" if n_drift_outside != 1 else ""
        reasons.append(f"{n_drift_outside} sleeve{plural} outside drift band")
    if measurable and max_drawdown_pct >= 0.8 * max_tolerable_drawdown:
        reasons.append(
            f"drawdown {max_drawdown_pct:.1%} >= 80% of "
            f"limit {max_tolerable_drawdown:.1%}"
        )
    if reasons:
        return RISK_WATCH, reasons

    return RISK_WITHIN, []


def classify_risk(
    *,
    n_violations: int,
    n_drift_outside: int,
    max_drawdown_pct: float | None,
    max_tolerable_drawdown: float | None,
    has_data: bool,
) -> str:
    """Status-only view of :func:`assess_risk`."""
    return assess_risk(
        n_violations=n_violations,
        n_drift_outside=n_drift_outside,
        max_drawdown_pct=max_drawdown_pct,
        max_tolerable_drawdown=max_tolerable_drawdown,
        has_data=has_data,
    )[0]


def max_drawdown(values: list[float]) -> float | None:
    """Largest peak-to-trough decline of ``values`` as a positive fraction.

    ``None`` when fewer than two points are available. Operates on raw snapshot
    values, so contributions/withdrawals within the window distort it — a known
    first-pass limitation (noted on the result), acceptable for a coarse risk
    signal.
    """
    if len(values) < 2:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        if value > peak:
            peak = value
        if peak > 0:
            decline = (peak - value) / peak
            if decline > worst:
                worst = decline
    return worst


def build_performance_period(
    *,
    portfolio_id: str,
    as_of_date: date,
    period: str,
    start_date: date | None,
    start_value: float | None,
    end_value: float | None,
    net_contributions: float,
    realized: float,
    dividends: float,
    target_return_pct: float | None,
    max_drawdown_pct: float | None,
    n_violations: int,
    n_drift_outside: int,
    max_tolerable_drawdown: float | None,
    has_risk_data: bool,
    extra_notes: list[str] | None = None,
) -> PerformancePeriod:
    """Assemble one progress row from already-gathered, DB-free inputs.

    This is the pure heart of the sprint: the job gathers snapshots,
    transactions, exposures, and drifts, then hands the scalars here. A missing
    ``start_value`` or ``end_value`` yields an ``insufficient_history`` row with
    no fabricated return number.
    """
    notes = list(extra_notes or [])
    risk_status, risk_reasons = assess_risk(
        n_violations=n_violations,
        n_drift_outside=n_drift_outside,
        max_drawdown_pct=max_drawdown_pct,
        max_tolerable_drawdown=max_tolerable_drawdown,
        has_data=has_risk_data,
    )

    if start_value is None or end_value is None:
        attribution = Attribution(
            net_contributions=net_contributions,
            realized=realized,
            dividends=dividends,
            market_movement=None,
            notes=notes,
        )
        return PerformancePeriod(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            period=period,
            start_value=start_value,
            end_value=end_value,
            net_contributions=net_contributions,
            investment_return=None,
            investment_return_pct=None,
            annualized_return_pct=None,
            target_return_pct=target_return_pct,
            return_gap_pct=None,
            max_drawdown_pct=max_drawdown_pct,
            risk_status=risk_status,
            status=GOAL_INSUFFICIENT,
            attribution=attribution,
            risk_reasons=risk_reasons,
            metadata={"annualized_return_pct": None},
        )

    investment_return, _adjusted, investment_return_pct = compute_investment_return(
        start_value, end_value, net_contributions
    )

    # ``start_date`` is the span used for annualization. For finite periods it is
    # the window boundary; for since_inception the caller passes the inception
    # date (earliest snapshot) even though start_value is 0. Without a date there
    # is no span to annualize over.
    days = (as_of_date - start_date).days if start_date is not None else 0
    annualized_pct = annualize_return(investment_return_pct, days) if days else None

    return_gap_pct = (
        annualized_pct - target_return_pct
        if annualized_pct is not None and target_return_pct is not None
        else None
    )
    status = classify_goal(annualized_pct, target_return_pct)

    market_movement = investment_return - realized - dividends
    attribution = Attribution(
        net_contributions=net_contributions,
        realized=realized,
        dividends=dividends,
        market_movement=market_movement,
        notes=notes,
    )

    return PerformancePeriod(
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        period=period,
        start_value=start_value,
        end_value=end_value,
        net_contributions=net_contributions,
        investment_return=investment_return,
        investment_return_pct=investment_return_pct,
        annualized_return_pct=annualized_pct,
        target_return_pct=target_return_pct,
        return_gap_pct=return_gap_pct,
        max_drawdown_pct=max_drawdown_pct,
        risk_status=risk_status,
        status=status,
        attribution=attribution,
        risk_reasons=risk_reasons,
        metadata={"annualized_return_pct": annualized_pct},
    )

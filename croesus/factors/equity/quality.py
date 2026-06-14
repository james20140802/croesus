"""
Quality factor metrics (pure).

Quality is the "control your junk" leg of the multi-factor model: profitable,
low-leverage companies. We compute three primitives from the cached fundamentals
and store them as factor_values; the screener percentile-ranks and blends them
into a ``quality_score`` (ROE and net margin higher-is-better, leverage
inverted), mirroring how valuation multiples are scored.

Quality is a *fundamental* factor, so — like valuation — it carries look-ahead
risk in a backtest and is used only in live screening + the forward-test.
"""
from __future__ import annotations

QUALITY_FACTOR_NAMES = ("roe", "net_margin", "debt_to_equity")


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def compute_quality_metrics(
    *,
    net_income: float | None,
    revenue: float | None,
    total_equity: float | None,
    total_debt: float | None,
) -> dict[str, float]:
    """Return the available quality primitives keyed by factor name.

    - ``roe`` = net_income / total_equity (profitability on equity)
    - ``net_margin`` = net_income / revenue (profitability on sales)
    - ``debt_to_equity`` = total_debt / total_equity (leverage; inverted in score)

    A metric whose inputs are missing or whose denominator is zero (or negative
    equity, which makes ROE/leverage meaningless) is simply omitted — never
    fabricated. The screener renormalizes around whatever is present.
    """
    metrics: dict[str, float] = {}
    # Negative or zero equity makes equity-based ratios meaningless; skip them.
    equity_ok = total_equity is not None and total_equity > 0

    roe = _safe_ratio(net_income, total_equity) if equity_ok else None
    if roe is not None:
        metrics["roe"] = roe

    net_margin = _safe_ratio(net_income, revenue)
    if net_margin is not None:
        metrics["net_margin"] = net_margin

    leverage = _safe_ratio(total_debt, total_equity) if equity_ok else None
    if leverage is not None:
        metrics["debt_to_equity"] = leverage

    return metrics

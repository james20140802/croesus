"""Backtest configuration dataclass and defaults."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration for a walk-forward backtest run.

    Parameters
    ----------
    start_date:
        First date of the backtest window (ISO string, e.g. "2021-01-01").
    end_date:
        Last date of the backtest window (ISO string).
    rebalance_frequency:
        Rebalancing cadence. Only ``'monthly'`` is supported (first trading
        day of each calendar month).
    top_n:
        Number of assets to hold per rebalance; ties broken by asset_id.
    cost_bps:
        One-way transaction cost in basis points. Round-trip cost on a
        changed weight is ``2 * cost_bps / 10_000``.
    rebalance_buffer:
        Churn hysteresis. An incumbent holding is retained while it stays
        within ``top_n * rebalance_buffer`` in the ranking, rather than being
        swapped for a marginally higher newcomer. ``1.0`` disables hysteresis
        (plain top-N); ``2.0`` keeps incumbents through the top ``2 * top_n``.
    weight_schemes:
        Mapping of scheme name → dimension weight dict. Each dict maps
        dimension name to weight. The standard dimensions are:
        ``momentum``, ``liquidity``, ``trend``, ``volatility_penalty``.
        Weights need not sum to 1; they are used as-is in the score
        formula matching run_screening (volatility_penalty is subtracted).
    benchmark_symbol:
        Ticker symbol for the buy-and-hold benchmark row (e.g. ``'SPY'``).
    initial_capital:
        Starting portfolio value in USD.
    """

    start_date: str
    end_date: str
    rebalance_frequency: str = "monthly"
    top_n: int = 5
    cost_bps: float = 10.0
    rebalance_buffer: float = 1.0
    weight_schemes: dict[str, dict[str, float]] = field(
        default_factory=lambda: _default_schemes()
    )
    benchmark_symbol: str = "SPY"
    initial_capital: float = 100_000.0


def _default_schemes() -> dict[str, dict[str, float]]:
    return {
        "composite_v1": {
            "momentum": 0.35,
            "liquidity": 0.25,
            "trend": 0.25,
            "volatility_penalty": 0.15,
        },
        "momentum_only": {
            "momentum": 1.0,
        },
    }


def default_config() -> BacktestConfig:
    """Return a BacktestConfig with the two canonical A/B weight schemes."""
    return BacktestConfig(
        start_date="2020-01-01",
        end_date="2024-12-31",
    )

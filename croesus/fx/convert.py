from __future__ import annotations


def to_base(
    amount: float,
    *,
    native_currency: str,
    base_currency: str,
    rates: dict[str, float],
) -> float:
    """Convert ``amount`` from native currency to base using rate-per-USD rows."""
    native = native_currency.upper()
    base = base_currency.upper()
    native_rate = rates.get(native, 1.0)
    base_rate = rates.get(base, 1.0)
    return amount * base_rate / native_rate

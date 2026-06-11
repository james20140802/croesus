from __future__ import annotations


class FxRateMissing(KeyError):
    """Raised when a required FX rate is absent instead of silently using 1:1.

    A silent 1.0 fallback once valued KRW cash at face-value USD (a ~1400x
    overstatement). Callers that truly want the old passthrough must opt in
    with ``fallback_to_one=True`` — and are expected to record an ERROR-level
    data-quality issue first.
    """

    def __init__(self, currency: str) -> None:
        super().__init__(currency)
        self.currency = currency

    def __str__(self) -> str:
        return f"no FX rate available for {self.currency}"


def to_base(
    amount: float,
    *,
    native_currency: str,
    base_currency: str,
    rates: dict[str, float],
    fallback_to_one: bool = False,
) -> float:
    """Convert ``amount`` from native currency to base using rate-per-USD rows.

    Raises ``FxRateMissing`` when either currency has no rate, unless
    ``fallback_to_one`` is set (explicit, audited opt-in only).
    """
    native = native_currency.upper()
    base = base_currency.upper()
    if not fallback_to_one:
        if native not in rates:
            raise FxRateMissing(native)
        if base not in rates:
            raise FxRateMissing(base)
    native_rate = rates.get(native, 1.0)
    base_rate = rates.get(base, 1.0)
    return amount * base_rate / native_rate

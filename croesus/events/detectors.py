from __future__ import annotations

from datetime import date

import pandas as pd

from croesus.events.models import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    EVENT_ABNORMAL_RETURN,
    EVENT_ABNORMAL_VOLUME,
    SOURCE_PRICES,
    Event,
)

VOLUME_WINDOW = 21
VOLUME_Z_THRESHOLD = 2.0
RETURN_WINDOW = 63
RETURN_SIGMA_MULT = 3.0


def _clean_prices(prices: pd.DataFrame) -> pd.DataFrame:
    data = prices.sort_values("date").copy()
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data["volume"] = pd.to_numeric(data["volume"], errors="coerce")
    return data.dropna(subset=["close", "volume"])


def detect_abnormal_volume(
    asset_id: str, as_of_date: date, prices: pd.DataFrame
) -> Event | None:
    """Latest volume ≥ VOLUME_Z_THRESHOLD σ above the trailing window mean.

    Spikes only — an unusually *low*-volume day is not a forward signal.
    """
    data = _clean_prices(prices)
    if len(data) < VOLUME_WINDOW + 1:
        return None
    volume = data["volume"]
    latest = float(volume.iloc[-1])
    baseline = volume.iloc[-(VOLUME_WINDOW + 1):-1]
    mean = float(baseline.mean())
    std = float(baseline.std())
    if std == 0:
        return None
    z = (latest - mean) / std
    if z < VOLUME_Z_THRESHOLD:
        return None
    return Event(
        asset_id=asset_id,
        as_of_date=as_of_date,
        event_type=EVENT_ABNORMAL_VOLUME,
        direction=DIRECTION_UP,
        magnitude=z,
        detail=f"volume {z:.1f}σ above {VOLUME_WINDOW}d mean",
        source=SOURCE_PRICES,
    )


def detect_abnormal_return(
    asset_id: str, as_of_date: date, prices: pd.DataFrame
) -> Event | None:
    """Latest daily return ≥ RETURN_SIGMA_MULT × trailing return volatility."""
    data = _clean_prices(prices)
    returns = data["close"].pct_change().dropna()
    if len(returns) < RETURN_WINDOW + 1:
        return None
    latest = float(returns.iloc[-1])
    baseline = returns.iloc[-(RETURN_WINDOW + 1):-1]
    sigma = float(baseline.std())
    if sigma == 0:
        return None
    sigma_mult = latest / sigma
    if abs(sigma_mult) < RETURN_SIGMA_MULT:
        return None
    direction = DIRECTION_UP if latest > 0 else DIRECTION_DOWN
    return Event(
        asset_id=asset_id,
        as_of_date=as_of_date,
        event_type=EVENT_ABNORMAL_RETURN,
        direction=direction,
        magnitude=sigma_mult,
        detail=f"return {latest:+.1%} = {sigma_mult:+.1f}σ vs {RETURN_WINDOW}d vol",
        source=SOURCE_PRICES,
    )

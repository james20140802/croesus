from __future__ import annotations

from datetime import date

import pandas as pd

from croesus.disclosures.models import Disclosure
from croesus.events.models import (
    DIRECTION_DOWN,
    DIRECTION_NEUTRAL,
    DIRECTION_UP,
    EVENT_ABNORMAL_RETURN,
    EVENT_ABNORMAL_VOLUME,
    EVENT_RECENT_DISCLOSURE,
    EVENT_VALUATION_DISLOCATION,
    SOURCE_DISCLOSURES,
    SOURCE_PRICES,
    SOURCE_VALUATION,
    Event,
)
from croesus.factors.equity.repository import ValuationSnapshot

VOLUME_WINDOW = 21
VOLUME_Z_THRESHOLD = 2.0
RETURN_WINDOW = 63
RETURN_SIGMA_MULT = 3.0
DISCLOSURE_WINDOW_DAYS = 7
VALUATION_DISLOCATION_PCT = 0.25


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


def detect_recent_disclosure(
    asset_id: str, as_of_date: date, disclosures: list[Disclosure]
) -> Event | None:
    """A filing dated within DISCLOSURE_WINDOW_DAYS at or before ``as_of_date``.

    Picks the most recent qualifying filing; the filing's existence is the
    signal (direction 'neutral' — reading intent is the LLM's job downstream).
    """
    in_window = [
        d
        for d in disclosures
        if d.filed_date <= as_of_date
        and (as_of_date - d.filed_date).days <= DISCLOSURE_WINDOW_DAYS
    ]
    if not in_window:
        return None
    most_recent = max(in_window, key=lambda d: d.filed_date)
    days_ago = (as_of_date - most_recent.filed_date).days
    return Event(
        asset_id=asset_id,
        as_of_date=as_of_date,
        event_type=EVENT_RECENT_DISCLOSURE,
        direction=DIRECTION_NEUTRAL,
        magnitude=float(days_ago),
        detail=f"{most_recent.form_type} filed {days_ago}d ago",
        source=SOURCE_DISCLOSURES,
    )


def detect_valuation_dislocation(
    asset_id: str, as_of_date: date, snapshot: ValuationSnapshot | None
) -> Event | None:
    """|upside_pct| ≥ VALUATION_DISLOCATION_PCT, read off the DCF snapshot.

    ``upside_pct`` > 0 means price is below intrinsic (an 'up' dislocation).
    """
    if snapshot is None or snapshot.upside_pct is None:
        return None
    upside = snapshot.upside_pct
    if abs(upside) < VALUATION_DISLOCATION_PCT:
        return None
    direction = DIRECTION_UP if upside > 0 else DIRECTION_DOWN
    return Event(
        asset_id=asset_id,
        as_of_date=as_of_date,
        event_type=EVENT_VALUATION_DISLOCATION,
        direction=direction,
        magnitude=upside,
        detail=f"price {upside:+.0%} vs DCF intrinsic",
        source=SOURCE_VALUATION,
    )


def detect_events(
    asset_id: str,
    as_of_date: date,
    prices: pd.DataFrame,
    snapshot: ValuationSnapshot | None,
    disclosures: list[Disclosure],
) -> list[Event]:
    """Run every detector for one asset; return the events that fired."""
    candidates = [
        detect_abnormal_volume(asset_id, as_of_date, prices),
        detect_abnormal_return(asset_id, as_of_date, prices),
        detect_recent_disclosure(asset_id, as_of_date, disclosures),
        detect_valuation_dislocation(asset_id, as_of_date, snapshot),
    ]
    return [e for e in candidates if e is not None]

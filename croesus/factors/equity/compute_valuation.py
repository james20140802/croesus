"""
Valuation orchestration (Sprint 007).

Reads prices, cached fundamentals, and the latest macro 10Y rate; calls the pure
math in :mod:`croesus.factors.equity.valuation`; and writes the eight valuation
factors to ``factor_values`` plus the detailed DCF to ``valuation_snapshots``.

Two cadences share one entry point via ``include_dcf``:
- daily (``include_dcf=False``): multiples + sector percentiles, which move with
  today's price.
- quarterly (``include_dcf=True``): also recompute the DCF and
  ``price_to_intrinsic`` against freshly-ingested statements.

Per-asset failures are logged and skipped — one bad name never stops the run.
This module computes and records; it never trades.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

import duckdb
import pandas as pd

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.factors.common import FactorValue
from croesus.factors.equity.repository import (
    ValuationSnapshot,
    ValuationSnapshotRepository,
)
from croesus.factors.equity.valuation import (
    DEFAULT_DCF_KNOBS,
    DEFAULT_RISK_FREE_RATE,
    ValuationMultiples,
    compute_beta,
    compute_fcf_growth,
    compute_multiples,
    sector_percentile,
    value_with_knobs,
)
from croesus.fundamentals.repository import (
    METRIC_BOOK_VALUE_PER_SHARE,
    METRIC_CASH_AND_EQUIVALENTS,
    METRIC_EBITDA,
    METRIC_EPS,
    METRIC_SHARES_OUTSTANDING,
    METRIC_TOTAL_DEBT,
    FundamentalsRepository,
)
from croesus.factors.equity.band_repository import (
    BandRow,
    IntrinsicValueBandRepository,
)
from croesus.factors.equity.intrinsic_bands import compute_intrinsic_bands
from croesus.prices.repository import PriceRepository
from croesus.research.thesis_repository import ThesisGradeRepository

BENCHMARK_SYMBOL = "SPY"
_BETA_LOOKBACK_DAYS = 730  # ~2 years of daily returns
_MIN_FCF_YEARS = 3
_DEFAULT_BETA = 1.0


@dataclass(frozen=True)
class ValuationComputationResult:
    computed: dict[str, int] = field(default_factory=dict)  # asset_id -> factors written
    dcf_computed: list[str] = field(default_factory=list)
    dcf_skipped: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


@dataclass
class _AssetCalc:
    asset: Asset
    price: float
    shares: float | None
    market_cap: float | None
    multiples: ValuationMultiples
    fundamentals: dict
    returns: dict[date, float]
    annual_fcf: list[float]
    beta: float | None = None


def compute_and_store_valuation_factors(
    conn: duckdb.DuckDBPyConnection,
    *,
    include_dcf: bool = True,
    as_of: date | None = None,
    log: Callable[[str], None] = print,
) -> ValuationComputationResult:
    as_of = as_of or date.today()
    assets = AssetRepository(conn).list_active(asset_type="equity", country="US")
    prices = PriceRepository(conn)
    fundamentals = FundamentalsRepository(conn)
    result = ValuationComputationResult()

    # Pass 1 — per-asset multiples (needs price + cached fundamentals).
    calcs: list[_AssetCalc] = []
    for asset in assets:
        try:
            calc = _build_multiples(
                asset, as_of, prices, fundamentals, with_returns=include_dcf
            )
            if calc is None:
                result.skipped[asset.asset_id] = "no price"
                log(f"skip valuation for {asset.symbol}: no price on or before {as_of}")
                continue
            calcs.append(calc)
        except Exception as exc:  # noqa: BLE001 - per-asset failures must not stop the run.
            result.failed[asset.asset_id] = str(exc)
            log(f"failed valuation for {asset.symbol}: {exc}")

    # Pass 2 — sector percentiles across the multiples we just built.
    percentiles = _sector_percentiles(calcs, as_of)

    # Pass 3 — DCF (quarterly cadence only), with a beta fallback chain.
    if include_dcf:
        _assign_betas(conn, calcs, prices, as_of)
        rf = _risk_free_rate(conn)
        # Band collaborators are only used on the DCF path — don't build them on
        # a daily multiples-only run (include_dcf=False).
        band_repo = IntrinsicValueBandRepository(conn)
        thesis_repo = ThesisGradeRepository(conn)

    snapshot_repo = ValuationSnapshotRepository(conn)
    for calc in calcs:
        asset_id = calc.asset.asset_id
        factors: list[FactorValue] = _multiple_factors(calc, as_of)
        factors += percentiles.get(asset_id, [])

        if include_dcf:
            try:
                price_to_intrinsic = _compute_dcf(
                    calc, as_of, rf=rf, snapshot_repo=snapshot_repo,
                    band_repo=band_repo, thesis_repo=thesis_repo,
                    result=result, log=log,
                )
                if price_to_intrinsic is not None:
                    factors.append(
                        FactorValue(asset_id, as_of, "price_to_intrinsic", price_to_intrinsic)
                    )
            except Exception as exc:  # noqa: BLE001
                result.dcf_skipped[asset_id] = str(exc)
                log(f"DCF failed for {calc.asset.symbol}: {exc}")

        _upsert_factor_values(conn, factors)
        result.computed[asset_id] = len(factors)
        log(f"stored {len(factors)} valuation factors for {calc.asset.symbol}")

    return result


def _build_multiples(
    asset: Asset,
    as_of: date,
    prices: PriceRepository,
    fundamentals: FundamentalsRepository,
    *,
    with_returns: bool,
) -> _AssetCalc | None:
    frame = prices.load_daily_prices(asset.asset_id)
    price = _latest_close(frame, as_of)
    if price is None:
        return None

    eps = fundamentals.get_latest_metric(asset.asset_id, METRIC_EPS)
    bvps = fundamentals.get_latest_metric(asset.asset_id, METRIC_BOOK_VALUE_PER_SHARE)
    ebitda = fundamentals.get_latest_metric(asset.asset_id, METRIC_EBITDA)
    total_debt = fundamentals.get_latest_metric(asset.asset_id, METRIC_TOTAL_DEBT)
    cash = fundamentals.get_latest_metric(asset.asset_id, METRIC_CASH_AND_EQUIVALENTS)
    shares = fundamentals.get_latest_metric(asset.asset_id, METRIC_SHARES_OUTSTANDING)
    annual_fcf = [v for _, v in fundamentals.get_annual_fcf(asset.asset_id)]
    latest_fcf = annual_fcf[-1] if annual_fcf else None
    market_cap = price * shares if shares else None

    multiples = compute_multiples(
        price=price,
        eps=eps,
        book_value_per_share=bvps,
        market_cap=market_cap,
        total_debt=total_debt,
        cash=cash,
        ebitda=ebitda,
        free_cash_flow=latest_fcf,
    )
    return _AssetCalc(
        asset=asset,
        price=price,
        shares=shares,
        market_cap=market_cap,
        multiples=multiples,
        fundamentals={
            "eps": eps,
            "book_value_per_share": bvps,
            "ebitda": ebitda,
            "total_debt": total_debt,
            "cash_and_equivalents": cash,
            "shares_outstanding": shares,
            "latest_fcf": latest_fcf,
        },
        returns=_daily_returns(frame, as_of) if with_returns else {},
        annual_fcf=annual_fcf,
    )


# (factor_name on the multiple, attribute on ValuationMultiples, percentile factor)
_MULTIPLE_SPECS = (
    ("pe_ratio", "pe_ratio", "pe_vs_sector_pct"),
    ("pb_ratio", "pb_ratio", "pb_vs_sector_pct"),
    ("ev_to_ebitda", "ev_to_ebitda", "ev_ebitda_vs_sector_pct"),
    ("fcf_yield", "fcf_yield", None),
)


def _multiple_factors(calc: _AssetCalc, as_of: date) -> list[FactorValue]:
    out: list[FactorValue] = []
    for factor_name, attr, _ in _MULTIPLE_SPECS:
        value = getattr(calc.multiples, attr)
        if value is not None:
            out.append(FactorValue(calc.asset.asset_id, as_of, factor_name, value))
    return out


def _sector_percentiles(
    calcs: list[_AssetCalc], as_of: date
) -> dict[str, list[FactorValue]]:
    out: dict[str, list[FactorValue]] = {}
    for _, attr, pct_name in _MULTIPLE_SPECS:
        if pct_name is None:
            continue
        # Group this multiple's values by sector (skip None sector / None value).
        by_sector: dict[str, list[tuple[str, float]]] = {}
        for calc in calcs:
            sector = calc.asset.sector
            value = getattr(calc.multiples, attr)
            if sector is None or value is None:
                continue
            by_sector.setdefault(sector, []).append((calc.asset.asset_id, value))
        for entries in by_sector.values():
            peers = [v for _, v in entries]
            for asset_id, value in entries:
                pct = sector_percentile(value, peers)
                if pct is not None:
                    out.setdefault(asset_id, []).append(
                        FactorValue(asset_id, as_of, pct_name, pct)
                    )
    return out


def _compute_dcf(
    calc: _AssetCalc,
    as_of: date,
    *,
    rf: float,
    snapshot_repo: ValuationSnapshotRepository,
    band_repo: IntrinsicValueBandRepository,
    thesis_repo: ThesisGradeRepository,
    result: ValuationComputationResult,
    log: Callable[[str], None],
) -> float | None:
    asset_id = calc.asset.asset_id
    if len(calc.annual_fcf) < _MIN_FCF_YEARS:
        result.dcf_skipped[asset_id] = "insufficient FCF history"
        log(f"skip DCF for {calc.asset.symbol}: fewer than {_MIN_FCF_YEARS} FCF years")
        return None
    if all(v <= 0 for v in calc.annual_fcf):
        result.dcf_skipped[asset_id] = "FCF negative across all periods"
        log(f"skip DCF for {calc.asset.symbol}: FCF negative across all periods")
        return None

    growth = compute_fcf_growth(calc.annual_fcf)
    if growth is None:
        result.dcf_skipped[asset_id] = "could not estimate FCF growth"
        log(f"skip DCF for {calc.asset.symbol}: could not estimate FCF growth")
        return None

    beta = calc.beta if calc.beta is not None else _DEFAULT_BETA
    knobs = DEFAULT_DCF_KNOBS  # Phase A: mechanical defaults; Phase C revises from thesis grades
    dcf = value_with_knobs(
        base_fcf=calc.annual_fcf[-1],
        growth_rate=growth,
        risk_free_rate=rf,
        beta=beta,
        shares_outstanding=calc.shares or 0.0,
        total_debt=calc.fundamentals["total_debt"],
        cash=calc.fundamentals["cash_and_equivalents"],
        knobs=knobs,
    )
    if dcf is None:
        result.dcf_skipped[asset_id] = "DCF inputs invalid (WACC<=g, base FCF<=0, or no shares)"
        log(f"skip DCF for {calc.asset.symbol}: invalid inputs (WACC<=g / base FCF<=0 / no shares)")
        return None

    upside_pct = dcf.intrinsic_value_per_share / calc.price - 1.0
    snapshot_repo.upsert(
        ValuationSnapshot(
            asset_id=asset_id,
            date=as_of,
            intrinsic_value_per_share=dcf.intrinsic_value_per_share,
            current_price=calc.price,
            upside_pct=upside_pct,
            wacc=dcf.wacc,
            fcf_growth_rate=dcf.fcf_growth_rate,
            terminal_growth_rate=dcf.terminal_growth_rate,
            assumptions={
                "source": "model",
                "risk_free_rate": rf,
                "beta": beta,
                "base_fcf": dcf.base_fcf,
                "enterprise_value": dcf.enterprise_value,
                "equity_value": dcf.equity_value,
                "explicit_years": knobs.explicit_years,
                "terminal_growth_rate": knobs.terminal_growth_rate,
                "wacc_risk_premium": knobs.wacc_risk_premium,
            },
        )
    )
    result.dcf_computed.append(asset_id)
    if dcf.intrinsic_value_per_share <= 0:
        return None  # negative intrinsic value -> price_to_intrinsic is meaningless
    # Band a viable base only — don't manufacture upside (a bull scenario could
    # overcome the same debt) for an asset whose base equity value is non-positive.
    _store_intrinsic_bands(
        calc, as_of, rf=rf, beta=beta, growth=growth,
        thesis_repo=thesis_repo, band_repo=band_repo, log=log,
    )
    return calc.price / dcf.intrinsic_value_per_share


def _store_intrinsic_bands(
    calc: _AssetCalc,
    as_of: date,
    *,
    rf: float,
    beta: float,
    growth: float,
    thesis_repo: ThesisGradeRepository,
    band_repo: IntrinsicValueBandRepository,
    log: Callable[[str], None],
) -> None:
    """Best-effort moat-adjusted band for an asset WITH a thesis grade.

    Grade-only: ungraded assets get no band. Reuses the same DCF inputs as the
    base snapshot but with grade-derived scenario knobs. Its failure must never
    disturb the base DCF / price_to_intrinsic, so all of it is caught here.
    """
    asset_id = calc.asset.asset_id
    try:
        grade = thesis_repo.load_latest_for_asset(asset_id, as_of)
        if grade is None:
            return  # no thesis -> no band (recommendation-only, shortlist-only)
        bands = compute_intrinsic_bands(
            base_fcf=calc.annual_fcf[-1],
            growth=growth,
            risk_free_rate=rf,
            beta=beta,
            shares_outstanding=calc.shares or 0.0,
            total_debt=calc.fundamentals["total_debt"],
            cash=calc.fundamentals["cash_and_equivalents"],
            moat=grade.moat_grade,
            sector=grade.sector_grade,
            disruption=grade.disruption_grade,
        )
        for scenario, band in bands.items():
            if band is None:
                # e.g. a low-β/low-rate asset whose bull WACC <= terminal growth.
                log(f"intrinsic band {scenario} skipped for {calc.asset.symbol}: invalid DCF")
                continue
            if band.intrinsic_value_per_share <= 0:
                # Negative equity value (highly leveraged) — meaningless, mirroring
                # the base DCF's price_to_intrinsic <= 0 guard. Don't persist it.
                log(f"intrinsic band {scenario} skipped for {calc.asset.symbol}: intrinsic <= 0")
                continue
            upside = (
                band.intrinsic_value_per_share / calc.price - 1.0
                if calc.price
                else None
            )
            band_repo.upsert_band(BandRow(
                asset_id=asset_id, date=as_of, scenario=scenario,
                intrinsic_value_per_share=band.intrinsic_value_per_share,
                current_price=calc.price, upside_pct=upside, wacc=band.wacc,
                fcf_growth_rate=band.fcf_growth_rate,
                terminal_growth_rate=band.terminal_growth_rate,
                explicit_years=band.explicit_years,
                wacc_risk_premium=band.wacc_risk_premium,
                moat_grade=grade.moat_grade, sector_grade=grade.sector_grade,
                disruption_grade=grade.disruption_grade,
                thesis_as_of_date=grade.as_of_date, thesis_run_id=grade.run_id,
            ))
    except Exception as exc:  # noqa: BLE001 - band is best-effort; base DCF stands.
        log(f"intrinsic band failed for {calc.asset.symbol}: {exc}")


def _assign_betas(
    conn: duckdb.DuckDBPyConnection,
    calcs: list[_AssetCalc],
    prices: PriceRepository,
    as_of: date,
) -> None:
    """Raw beta vs SPY where possible, then sector-median, then 1.0 (spec §4)."""
    market_returns = _benchmark_returns(conn, prices, as_of)
    if market_returns:
        for calc in calcs:
            aligned_asset, aligned_market = _align_returns(calc.returns, market_returns)
            calc.beta = compute_beta(aligned_asset, aligned_market)

    # Sector-median fill for assets still missing a beta.
    sector_betas: dict[str, list[float]] = {}
    for calc in calcs:
        if calc.beta is not None and calc.asset.sector is not None:
            sector_betas.setdefault(calc.asset.sector, []).append(calc.beta)
    for calc in calcs:
        if calc.beta is None:
            peers = sector_betas.get(calc.asset.sector or "")
            calc.beta = statistics.median(peers) if peers else None


def _benchmark_returns(
    conn: duckdb.DuckDBPyConnection, prices: PriceRepository, as_of: date
) -> dict[date, float]:
    row = conn.execute(
        "SELECT asset_id FROM assets WHERE symbol = ? LIMIT 1", [BENCHMARK_SYMBOL]
    ).fetchone()
    if row is None:
        return {}
    frame = prices.load_daily_prices(row[0])
    return _daily_returns(frame, as_of)


def _risk_free_rate(conn: duckdb.DuckDBPyConnection) -> float:
    try:
        row = conn.execute(
            "SELECT raw_indicators FROM macro_scores ORDER BY date DESC LIMIT 1"
        ).fetchone()
    except Exception:  # noqa: BLE001 - macro table may be absent.
        return DEFAULT_RISK_FREE_RATE
    if row is None or row[0] is None:
        return DEFAULT_RISK_FREE_RATE
    raw = row[0]
    if isinstance(raw, str):
        import json

        raw = json.loads(raw)
    dgs10 = raw.get("DGS10") if isinstance(raw, dict) else None
    if dgs10 is None:
        return DEFAULT_RISK_FREE_RATE
    return float(dgs10) / 100.0  # DGS10 is stored in percent


def _latest_close(frame: pd.DataFrame, as_of: date) -> float | None:
    if frame.empty:
        return None
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.date
    data = data[(data["date"] <= as_of)].dropna(subset=["close"])
    if data.empty:
        return None
    return float(data.iloc[-1]["close"])


def _daily_returns(frame: pd.DataFrame, as_of: date) -> dict[date, float]:
    if frame.empty:
        return {}
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.date
    cutoff = as_of - timedelta(days=_BETA_LOOKBACK_DAYS)
    data = data[(data["date"] <= as_of) & (data["date"] > cutoff)].dropna(subset=["close"])
    data = data.sort_values("date")
    if len(data) < 2:
        return {}
    closes = data["close"].astype(float).to_numpy()
    dates = list(data["date"])
    returns: dict[date, float] = {}
    for i in range(1, len(closes)):
        if closes[i - 1] != 0:
            returns[dates[i]] = closes[i] / closes[i - 1] - 1.0
    return returns


def _align_returns(
    asset_returns: dict[date, float], market_returns: dict[date, float]
) -> tuple[list[float], list[float]]:
    common = sorted(set(asset_returns) & set(market_returns))
    return ([asset_returns[d] for d in common], [market_returns[d] for d in common])


def _upsert_factor_values(
    conn: duckdb.DuckDBPyConnection, factor_values: list[FactorValue]
) -> None:
    if not factor_values:
        return
    conn.executemany(
        """
        INSERT INTO factor_values (asset_id, date, factor_name, value)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (asset_id, date, factor_name) DO UPDATE SET
          value = excluded.value
        """,
        [(f.asset_id, f.date, f.factor_name, f.value) for f in factor_values],
    )

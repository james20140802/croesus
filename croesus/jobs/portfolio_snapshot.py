from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Sequence

import duckdb

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.fx.repository import FxRepository
from croesus.portfolio.exposure import ExposureLimits, compute_exposures
from croesus.portfolio.import_holdings import load_holdings_csv
from croesus.portfolio.mark_to_market import mark_to_market
from croesus.portfolio.models import AssetAttrs, Portfolio, PortfolioSnapshotResult, is_cash
from croesus.portfolio.policy import compute_policy_drifts
from croesus.portfolio.repository import PortfolioRepository
from croesus.prices.repository import PriceRepository
from croesus.profiles.models import InvestorProfile
from croesus.profiles.repository import ProfileRepository

_DEFAULT_PORTFOLIO_ID = "default"


def run_portfolio_snapshot(
    conn: duckdb.DuckDBPyConnection,
    holdings_path: str | Path,
    *,
    portfolio_id: str = _DEFAULT_PORTFOLIO_ID,
    as_of_date: date | None = None,
    log: Callable[[str], None] = print,
) -> PortfolioSnapshotResult:
    """Import holdings, compute exposure and drift, persist the snapshot.

    Expects an already-migrated connection. Resolves the portfolio's profile
    for concentration limits and policy targets, persists holdings, exposures,
    drifts, and a snapshot row, then returns the full result. Unknown or
    malformed holdings are skipped (never fatal) so a partial book still yields
    a snapshot.
    """
    as_of = as_of_date or date.today()
    profile_repo = ProfileRepository(conn)
    portfolio_repo = PortfolioRepository(conn)

    profile = _resolve_profile(conn, portfolio_repo, profile_repo, portfolio_id)
    base_currency = profile.base_currency.value if profile else "USD"
    base_country = (profile.metadata.get("base_country") if profile else None) or "US"

    portfolio = _ensure_portfolio(portfolio_repo, portfolio_id, profile, base_currency)

    # Pass the target portfolio and its governing base currency down so rows
    # omitting those columns adopt the right defaults (not the DB default
    # profile) and rows for other portfolios are skipped + counted honestly.
    imported = load_holdings_csv(
        holdings_path,
        conn,
        as_of,
        portfolio_id=portfolio_id,
        base_currency=base_currency,
    )
    warnings = list(imported.warnings)
    raw_holdings = imported.holdings

    assets_by_id = _load_asset_attrs(conn, [h.asset_id for h in raw_holdings])
    price_repo = PriceRepository(conn)
    fx_rates = _load_fx_rates(
        conn,
        _required_currencies(raw_holdings, base_currency),
        as_of,
    )
    mark_result = mark_to_market(
        raw_holdings,
        price_lookup=lambda asset_id: price_repo.get_latest_close(asset_id, as_of),
        fx_rates=fx_rates,
        assets_by_id=assets_by_id,
        base_currency=base_currency,
        as_of_date=as_of,
    )
    warnings.extend(mark_result.warnings)
    holdings = mark_result.holdings

    portfolio_repo.replace_holdings(portfolio_id, as_of, holdings)

    total_mv = mark_result.total_market_value
    cash_value = sum((h.market_value or 0.0) for h in holdings if is_cash(h.asset_id))
    limits = _limits_from_profile(profile)

    exposures = compute_exposures(
        holdings,
        assets_by_id,
        limits,
        portfolio_id=portfolio_id,
        as_of_date=as_of,
        base_currency=base_currency,
        base_country=base_country,
    )
    portfolio_repo.replace_exposures(portfolio_id, as_of, exposures)

    targets = profile_repo.get_policy_targets(portfolio.profile_id)
    drift_result = compute_policy_drifts(
        holdings,
        assets_by_id,
        targets,
        portfolio_id=portfolio_id,
        as_of_date=as_of,
    )
    portfolio_repo.replace_drifts(portfolio_id, as_of, drift_result.drifts)
    warnings.extend(drift_result.warnings)

    portfolio_repo.save_snapshot(
        portfolio_id,
        as_of,
        total_mv,
        total_cost_basis=mark_result.total_cost_basis,
        unrealized_pnl=mark_result.unrealized_pnl,
        cash_value=cash_value,
    )

    result = PortfolioSnapshotResult(
        portfolio_id=portfolio_id,
        as_of_date=as_of,
        total_market_value=total_mv,
        total_cost_basis=mark_result.total_cost_basis,
        unrealized_pnl=mark_result.unrealized_pnl,
        holdings_imported=len(holdings),
        holdings_skipped=imported.skipped,
        exposures=exposures,
        policy_drifts=drift_result.drifts,
        warnings=warnings,
    )
    _log_summary(result, log)
    return result


def _resolve_profile(
    conn: duckdb.DuckDBPyConnection,
    portfolio_repo: PortfolioRepository,
    profile_repo: ProfileRepository,
    portfolio_id: str,
) -> InvestorProfile | None:
    """Find the profile to govern this snapshot.

    Prefers the profile already linked to the portfolio, then the ``default``
    profile, then any profile. Returns None when no profile exists (the run
    still proceeds with no limits).
    """
    existing = portfolio_repo.get_portfolio(portfolio_id)
    if existing is not None:
        return profile_repo.get_profile(existing.profile_id)

    profile = profile_repo.get_profile(_DEFAULT_PORTFOLIO_ID)
    if profile is not None:
        return profile

    row = conn.execute(
        "SELECT profile_id FROM investor_profiles ORDER BY profile_id LIMIT 1"
    ).fetchone()
    return profile_repo.get_profile(row[0]) if row else None


def _ensure_portfolio(
    portfolio_repo: PortfolioRepository,
    portfolio_id: str,
    profile: InvestorProfile | None,
    base_currency: str,
) -> Portfolio:
    existing = portfolio_repo.get_portfolio(portfolio_id)
    if existing is not None:
        return existing
    portfolio = Portfolio(
        portfolio_id=portfolio_id,
        profile_id=profile.profile_id if profile else _DEFAULT_PORTFOLIO_ID,
        name=f"{portfolio_id} portfolio",
        base_currency=base_currency,
    )
    portfolio_repo.upsert_portfolio(portfolio)
    return portfolio


def _load_asset_attrs(
    conn: duckdb.DuckDBPyConnection, asset_ids: list[str]
) -> dict[str, AssetAttrs]:
    lookup = [a for a in set(asset_ids) if not is_cash(a)]
    if not lookup:
        return {}
    placeholders = ", ".join("?" for _ in lookup)
    rows = conn.execute(
        f"""
        SELECT asset_id, asset_type, sector, industry, country, currency, metadata
        FROM assets WHERE asset_id IN ({placeholders})
        """,
        lookup,
    ).fetchall()
    attrs: dict[str, AssetAttrs] = {}
    for asset_id, asset_type, sector, industry, country, currency, metadata in rows:
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        theme_tags = (metadata or {}).get("theme_tags") or []
        attrs[asset_id] = AssetAttrs(
            asset_type=asset_type,
            sector=sector,
            industry=industry,
            country=country,
            currency=currency,
            theme_tags=list(theme_tags),
        )
    return attrs


def _required_currencies(holdings: list, base_currency: str) -> set[str]:
    currencies = {base_currency.upper()}
    for holding in holdings:
        if holding.currency:
            currencies.add(holding.currency.upper())
        if is_cash(holding.asset_id):
            currency = holding.asset_id.removeprefix("CASH_")
            if currency:
                currencies.add(currency.upper())
    return currencies


def _load_fx_rates(
    conn: duckdb.DuckDBPyConnection,
    currencies: set[str],
    as_of: date,
) -> dict[str, float]:
    repo = FxRepository(conn)
    rates: dict[str, float] = {}
    for currency in sorted(currencies | {"USD"}):
        rate = repo.get_latest_rate(currency, as_of)
        if rate is not None:
            rates[currency] = rate
    return rates


def _limits_from_profile(profile: InvestorProfile | None) -> ExposureLimits:
    if profile is None:
        return ExposureLimits()
    return ExposureLimits(
        max_single_position_weight=profile.max_single_position_weight,
        max_sector_weight=profile.max_sector_weight,
        max_industry_weight=profile.max_industry_weight,
        max_theme_weight=profile.max_theme_weight,
        max_country_weight=profile.max_country_weight,
        max_currency_weight=profile.max_currency_weight,
    )


def _log_summary(result: PortfolioSnapshotResult, log: Callable[[str], None]) -> None:
    log(
        f"portfolio {result.portfolio_id} @ {result.as_of_date}: "
        f"total={result.total_market_value:.2f} "
        f"cost={result.total_cost_basis:.2f} "
        f"pnl={result.unrealized_pnl:.2f} "
        f"imported={result.holdings_imported} skipped={result.holdings_skipped}"
    )
    violations = [e for e in result.exposures if e.is_violation]
    if violations:
        log(f"concentration violations ({len(violations)}):")
        for e in violations:
            log(f"  {e.exposure_type}:{e.exposure_name} {e.weight:.2%} > {e.limit_weight:.2%}")
    outside = [d for d in result.policy_drifts if d.is_outside_band]
    if outside:
        log(f"policy sleeves outside band ({len(outside)}):")
        for d in outside:
            log(f"  {d.sleeve_name} {d.current_weight:.2%} (target {d.target_weight:.2%})")
    for warning in result.warnings:
        log(f"warning: {warning}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.portfolio_snapshot",
        description=(
            "Import a manual holdings CSV, compute exposure and policy drift, "
            "and persist a portfolio snapshot."
        ),
    )
    parser.add_argument(
        "--holdings",
        required=True,
        metavar="PATH",
        help="path to the holdings CSV to import",
    )
    parser.add_argument(
        "--portfolio-id",
        default=_DEFAULT_PORTFOLIO_ID,
        help="portfolio to snapshot (default: %(default)s)",
    )
    parser.add_argument(
        "--date",
        dest="as_of_date",
        metavar="YYYY-MM-DD",
        help="snapshot date (default: today)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    as_of = None
    if args.as_of_date:
        try:
            as_of = date.fromisoformat(args.as_of_date)
        except ValueError as exc:
            print(f"invalid --date: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    migrate()
    with get_connection() as conn:
        try:
            run_portfolio_snapshot(
                conn,
                args.holdings,
                portfolio_id=args.portfolio_id,
                as_of_date=as_of,
            )
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

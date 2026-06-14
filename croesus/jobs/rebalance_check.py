from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Sequence
from uuid import uuid4

import duckdb

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.macro._loader import load_latest_macro_state
from croesus.portfolio.actions import ProposedAction, RebalanceRunResult
from croesus.portfolio.models import AssetAttrs
from croesus.portfolio.rebalancing import generate_proposed_actions
from croesus.portfolio.repository import PortfolioRepository
from croesus.profiles.repository import ProfileRepository
from croesus.reports.portfolio_action import write_portfolio_action_reports
from croesus.research.agent import generate_research_notes
from croesus.research.llm_client import ChatClient
from croesus.screening.repository import ScreeningRepository

_DEFAULT_PORTFOLIO_ID = "default"
_DEFAULT_PROFILE_ID = "default"


def run_rebalance_check(
    conn: duckdb.DuckDBPyConnection,
    *,
    portfolio_id: str = _DEFAULT_PORTFOLIO_ID,
    profile_id: str = _DEFAULT_PROFILE_ID,
    as_of_date: date | None = None,
    reports_dir: str | Path = "reports",
    llm_client: ChatClient | None = None,
    log: Callable[[str], None] = print,
) -> RebalanceRunResult:
    """Generate proposed actions, write portfolio action reports, and return result.

    Actions flagged ``requires_research`` get a local-LLM research note
    (Sprint 010) attached to the report; an unreachable LLM server only logs a
    warning — the run itself never blocks on it. ``llm_client`` is injectable
    for tests.
    """
    profile_repo = ProfileRepository(conn)
    portfolio_repo = PortfolioRepository(conn)

    profile = profile_repo.get_profile(profile_id)
    if profile is None:
        raise ValueError(f"profile not found: {profile_id}")

    snapshot = _load_snapshot(conn, portfolio_id, as_of_date)
    as_of = snapshot["as_of_date"]
    total_market_value = snapshot["total_market_value"] or 0.0
    holdings = portfolio_repo.get_holdings(portfolio_id, as_of)
    exposures = portfolio_repo.get_exposures(portfolio_id, as_of)
    drifts = portfolio_repo.get_drifts(portfolio_id, as_of)
    macro_state = load_latest_macro_state(conn)
    screening_run_id = _latest_screening_run_id(conn)
    screening_candidates = (
        ScreeningRepository(conn).list_results(screening_run_id)
        if screening_run_id
        else []
    )
    asset_ids = [h.asset_id for h in holdings] + [c.asset_id for c in screening_candidates]
    assets_by_id = _load_asset_attrs(conn, asset_ids)

    run_id = f"rebalance-{as_of:%Y%m%d}-{uuid4().hex[:8]}"
    actions = generate_proposed_actions(
        run_id,
        portfolio_id=portfolio_id,
        as_of_date=as_of,
        profile=profile,
        total_market_value=total_market_value,
        exposures=exposures,
        drifts=drifts,
        holdings=holdings,
        assets_by_id=assets_by_id,
        screening_candidates=screening_candidates,
        macro_state=macro_state,
    )
    decision = _decision(actions)
    summary = _summary(actions)
    metadata = {
        "latest_macro_state_date": (
            macro_state.date.isoformat() if macro_state is not None else None
        ),
        "latest_portfolio_snapshot_date": as_of.isoformat(),
        "latest_screening_run_id": screening_run_id,
    }
    portfolio_repo.upsert_rebalance_run(
        run_id,
        portfolio_id,
        profile_id,
        as_of,
        decision=decision,
        summary=summary,
        macro_regime=getattr(macro_state, "regime", None),
        macro_positioning=getattr(macro_state, "positioning", None),
        metadata=metadata,
    )
    portfolio_repo.replace_proposed_actions(run_id, actions)
    # Notes only annotate persisted proposals; they are generated before the
    # report so the report can render them, and never alter the actions.
    generate_research_notes(
        conn,
        run_id=run_id,
        as_of_date=as_of,
        actions=actions,
        screening_candidates=screening_candidates,
        macro_state=macro_state,
        client=llm_client,
        log=log,
    )
    markdown_path, csv_path = write_portfolio_action_reports(
        conn, run_id, reports_dir=reports_dir
    )
    result = RebalanceRunResult(
        run_id=run_id,
        portfolio_id=portfolio_id,
        profile_id=profile_id,
        as_of_date=as_of,
        decision=decision,
        actions=actions,
        markdown_report_path=markdown_path,
        csv_report_path=csv_path,
    )
    log(f"{decision}: {summary}")
    log(f"Markdown report: {markdown_path}")
    log(f"CSV report: {csv_path}")
    return result


def _load_snapshot(
    conn: duckdb.DuckDBPyConnection,
    portfolio_id: str,
    as_of_date: date | None,
) -> dict:
    if as_of_date is not None:
        snapshot = PortfolioRepository(conn).get_snapshot(portfolio_id, as_of_date)
        if snapshot is None:
            raise ValueError(
                f"portfolio snapshot not found for {portfolio_id} on {as_of_date}"
            )
        return snapshot

    row = conn.execute(
        """
        SELECT as_of_date
        FROM portfolio_snapshots
        WHERE portfolio_id = ?
        ORDER BY as_of_date DESC
        LIMIT 1
        """,
        [portfolio_id],
    ).fetchone()
    if row is None:
        raise ValueError(f"portfolio snapshot not found for {portfolio_id}")
    snapshot = PortfolioRepository(conn).get_snapshot(portfolio_id, row[0])
    if snapshot is None:
        raise ValueError(f"portfolio snapshot not found for {portfolio_id}")
    return snapshot


def _latest_screening_run_id(conn: duckdb.DuckDBPyConnection) -> str | None:
    row = conn.execute(
        """
        SELECT run_id
        FROM screening_results
        GROUP BY run_id
        ORDER BY run_id DESC
        LIMIT 1
        """
    ).fetchone()
    return row[0] if row else None


def _load_asset_attrs(
    conn: duckdb.DuckDBPyConnection, asset_ids: list[str]
) -> dict[str, AssetAttrs]:
    lookup = [asset_id for asset_id in sorted(set(asset_ids)) if not asset_id.startswith("CASH_")]
    if not lookup:
        return {}
    placeholders = ", ".join("?" for _ in lookup)
    rows = conn.execute(
        f"""
        SELECT asset_id, asset_type, sector, industry, country, currency, name, metadata
        FROM assets
        WHERE asset_id IN ({placeholders})
        """,
        lookup,
    ).fetchall()
    attrs: dict[str, AssetAttrs] = {}
    for asset_id, asset_type, sector, industry, country, currency, name, metadata in rows:
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        attrs[asset_id] = AssetAttrs(
            asset_type=asset_type,
            sector=sector,
            industry=industry,
            country=country,
            currency=currency,
            theme_tags=list((metadata or {}).get("theme_tags") or []),
            name=name,
        )
    return attrs


def _decision(actions: list[ProposedAction]) -> str:
    if any("PROFILE_INVALID" in action.reason_codes for action in actions):
        return "profile_invalid"
    if any(action.action_type in {"trim", "add", "rebalance_to_band", "raise_cash"} for action in actions):
        return "rebalance_recommended"
    if any(action.requires_research for action in actions):
        return "research_required"
    return "no_action"


def _summary(actions: list[ProposedAction]) -> str:
    trade_actions = [
        action
        for action in actions
        if action.action_type in {"trim", "add", "rebalance_to_band", "raise_cash"}
    ]
    if trade_actions:
        return f"{len(trade_actions)} trade action(s) proposed."
    return f"{len(actions)} non-trade action(s) generated."


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a portfolio action report.")
    parser.add_argument("--portfolio-id", default=_DEFAULT_PORTFOLIO_ID)
    parser.add_argument("--profile-id", default=_DEFAULT_PROFILE_ID)
    parser.add_argument("--date", dest="as_of_date")
    parser.add_argument("--reports-dir", default="reports")
    args = parser.parse_args(argv)

    migrate()
    as_of = date.fromisoformat(args.as_of_date) if args.as_of_date else None
    with get_connection() as conn:
        try:
            run_rebalance_check(
                conn,
                portfolio_id=args.portfolio_id,
                profile_id=args.profile_id,
                as_of_date=as_of,
                reports_dir=args.reports_dir,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

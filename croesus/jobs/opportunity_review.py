"""CLI for the opportunity-engine methodology selector and review surface.

This is a human-run review command. It reads persisted opportunity-engine
outputs and formats them for inspection; it never writes portfolio actions or
submits trades.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Sequence

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.opportunities.review import run_opportunity_review
from croesus.opportunities.risk_gate import DEFAULT_MIN_LIQUIDITY_USD
from croesus.opportunities.selection import (
    MethodologyUnavailable,
    OPPORTUNITY_METHODOLOGIES,
    OpportunityPrompter,
    select_methodology,
)
from croesus.reports.opportunity import (
    render_opportunity_review,
    write_opportunity_review_report,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.opportunity_review",
        description=(
            "Select an opportunity-engine methodology and render its current "
            "recommendation-only review. No trades."
        ),
    )
    parser.add_argument(
        "--methodology",
        choices=sorted(OPPORTUNITY_METHODOLOGIES),
        help="methodology to run (default: interactive menu)",
    )
    parser.add_argument("--date", dest="as_of", metavar="YYYY-MM-DD", help="as-of date")
    parser.add_argument("--limit", type=int, default=20, help="maximum cards to render")
    parser.add_argument("--db-path", default=None, help="DuckDB path")
    parser.add_argument("--report", action="store_true", help="write reports/opportunity/")
    parser.add_argument(
        "--portfolio-id", default="default", help="portfolio for the risk gate"
    )
    parser.add_argument(
        "--profile-id", default="default", help="profile for the risk gate"
    )
    parser.add_argument(
        "--no-risk-gate", dest="apply_risk_gate", action="store_false",
        help="skip the portfolio risk-gate check",
    )
    parser.add_argument(
        "--min-liquidity-usd", type=float, default=DEFAULT_MIN_LIQUIDITY_USD,
        help="liquidity floor (21d mean $ volume); 0 disables the liquidity warn",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    prompter: OpportunityPrompter | None = None,
) -> None:
    args = _build_parser().parse_args(argv)
    as_of = date.fromisoformat(args.as_of) if args.as_of else None

    try:
        methodology = select_methodology(args.methodology, prompter=prompter)
    except MethodologyUnavailable as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc

    migrate(args.db_path)
    with get_connection(args.db_path) as conn:
        result = run_opportunity_review(
            conn,
            methodology=methodology,
            as_of_date=as_of,
            limit=args.limit,
            portfolio_id=args.portfolio_id,
            profile_id=args.profile_id,
            apply_risk_gate=args.apply_risk_gate,
            min_liquidity_usd=args.min_liquidity_usd,
        )
        print(render_opportunity_review(result))
        if args.report:
            path = write_opportunity_review_report(result, conn=conn)
            print(f"wrote {path}")


if __name__ == "__main__":
    main()

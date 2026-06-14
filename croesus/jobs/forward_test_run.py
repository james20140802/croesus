"""
CLI for the forward-test harness.

Two modes, both human-run (never scheduled into local_sync — this is an
experiment track, not part of the production pipeline):

    # Record today's cohort for every candidate scheme (run periodically, e.g.
    # monthly, to build the track record):
    python -m croesus.jobs.forward_test_run --record

    # Record one scheme:
    python -m croesus.jobs.forward_test_run --record --scheme composite_v2_value

    # Evaluate all recorded cohorts to date and write the track-record report:
    python -m croesus.jobs.forward_test_run --evaluate --report

Valuation-based schemes cannot be backtested (look-ahead), so this accumulates
honest out-of-sample evidence over time. Recording a (scheme, date) again
replaces that cohort.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Sequence

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.forward_test.run import evaluate_cohorts, record_cohort
from croesus.forward_test.schemes import FORWARD_TEST_SCHEMES


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.forward_test_run",
        description=(
            "Record and evaluate forward-test cohorts for candidate weight "
            "schemes. Out-of-sample only — never trades."
        ),
    )
    parser.add_argument("--record", action="store_true", help="record cohort(s) today")
    parser.add_argument("--evaluate", action="store_true", help="evaluate recorded cohorts")
    parser.add_argument(
        "--scheme",
        choices=sorted(FORWARD_TEST_SCHEMES),
        help="restrict --record/--evaluate to one scheme (default: all)",
    )
    parser.add_argument("--date", dest="as_of", metavar="YYYY-MM-DD", help="as-of date")
    parser.add_argument("--report", action="store_true", help="with --evaluate, write reports/")
    parser.add_argument("--db-path", default=None, help="DuckDB path")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if not (args.record or args.evaluate):
        print("nothing to do: pass --record and/or --evaluate", file=sys.stderr)
        raise SystemExit(2)

    as_of = date.fromisoformat(args.as_of) if args.as_of else None

    migrate(args.db_path)
    with get_connection(args.db_path) as conn:
        if args.record:
            schemes = [args.scheme] if args.scheme else list(FORWARD_TEST_SCHEMES)
            for scheme in schemes:
                record_cohort(conn, scheme, as_of_date=as_of)

        if args.evaluate:
            results = evaluate_cohorts(conn, eval_date=as_of, scheme=args.scheme)
            for r in results:
                cr = "n/a" if r.cohort_return is None else f"{r.cohort_return:+.2%}"
                xs = "n/a" if r.excess_return is None else f"{r.excess_return:+.2%}"
                print(
                    f"{r.cohort_scheme:22} @ {r.as_of_date} "
                    f"d={r.days_held:>4} priced={r.n_priced}/{r.n_picks} "
                    f"return={cr} excess_vs_spy={xs}"
                )
            if args.report:
                from croesus.reports.forward_test import write_forward_test_reports

                eval_date = as_of or date.today()
                md, csv_path = write_forward_test_reports(
                    results, eval_date=eval_date, conn=conn
                )
                print(f"wrote {md}")
                print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()

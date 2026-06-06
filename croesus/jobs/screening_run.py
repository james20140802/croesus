from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Callable

import duckdb

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.macro._loader import load_latest_macro_state
from croesus.macro.screening_adapter import get_screening_params, neutral_screening_params
from croesus.screening.models import ScreeningRunResult
from croesus.screening.report import save_report
from croesus.screening.run_screening import run_screening


def run_screening_job(
    conn: duckdb.DuckDBPyConnection,
    *,
    as_of_date: date | None = None,
    portfolio_id: str | None = None,
    log: Callable[[str], None] = print,
) -> ScreeningRunResult:
    """Load screening params, rank active assets, persist results, and return them."""
    macro_state = load_latest_macro_state(conn)
    if macro_state is None:
        log("no MacroState found; using neutral screening params")
        screening_params = neutral_screening_params()
    else:
        screening_params = get_screening_params(macro_state)

    result = run_screening(
        conn,
        screening_params,
        as_of_date=as_of_date,
        portfolio_id=portfolio_id,
    )
    log(
        "screening complete: "
        f"{len(result.candidates)} ranked, {len(result.skipped)} skipped"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m croesus.jobs.screening_run")
    parser.add_argument("--save-report", action="store_true", help="write Markdown and CSV reports")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"), help="report root directory")
    parser.add_argument("--portfolio-id", help="optional portfolio exposure overlay")
    args = parser.parse_args()

    migrate()
    with get_connection() as conn:
        result = run_screening_job(conn, portfolio_id=args.portfolio_id)
        report_paths = None
        if args.save_report:
            report_paths = save_report(
                conn,
                result,
                reports_dir=args.reports_dir,
                portfolio_id=args.portfolio_id,
            )

    print(f"screening run: {result.run_id} as_of={result.as_of_date.isoformat()}")
    print("top candidates:")
    for candidate in result.candidates[:10]:
        score = "n/a" if candidate.score is None else f"{candidate.score:.4f}"
        rank = "n/a" if candidate.rank is None else str(candidate.rank)
        print(
            f"{rank}. {candidate.asset_id} score={score} "
            f"bucket={candidate.decision_bucket} reason={candidate.reason}"
        )
    if result.skipped:
        print("skipped assets:")
        for candidate in result.skipped:
            print(f"- {candidate.asset_id}: {candidate.reason}")
    if report_paths is not None:
        md_path, csv_path = report_paths
        print(f"reports written: {md_path} and {csv_path}")


if __name__ == "__main__":
    main()

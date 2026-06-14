"""Forward-test track-record report (Markdown + CSV).

Renders the realized, out-of-sample performance of every recorded cohort vs
SPY. Every cohort is honestly labeled with its age — early cohorts carry little
evidence, and the report says so rather than implying a verdict from days of
data.
"""
from __future__ import annotations

import csv
from pathlib import Path

from croesus.forward_test.models import CohortReturn
from croesus.reports.paths import report_output_dir
from croesus.reports.registry import register_many

REPORT_TYPE_FORWARD_TEST = "forward_test"

_CSV_FIELDS = [
    "cohort_scheme", "as_of_date", "eval_date", "days_held",
    "n_picks", "n_priced", "cohort_return", "benchmark_return", "excess_return",
]


def write_forward_test_reports(
    results: list[CohortReturn],
    *,
    eval_date,
    reports_dir: str | Path = "reports",
    conn=None,
) -> tuple[Path, Path]:
    output_dir = report_output_dir(reports_dir, "forward_test", eval_date)
    md_path = output_dir / "forward_test.md"
    csv_path = output_dir / "forward_test.csv"
    md_path.write_text(render_markdown(results, eval_date), encoding="utf-8")
    _write_csv(csv_path, results)
    if conn is not None:
        register_many(conn, REPORT_TYPE_FORWARD_TEST, [md_path, csv_path], as_of_date=eval_date)
    return md_path, csv_path


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:+.2f}%"


def render_markdown(results: list[CohortReturn], eval_date) -> str:
    lines = [
        f"# Forward-Test Track Record — as of {eval_date:%Y-%m-%d}",
        "",
        "> Out-of-sample only: every cohort records what a scheme would have "
        "bought on its date; returns are realized forward from stored prices vs "
        "SPY. No look-ahead. **Cohorts under ~3 months carry little evidence** — "
        "read the age column before any verdict.",
        "",
        "| Scheme | Cohort date | Days | Picks priced | Return | SPY | Excess |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda x: (x.cohort_scheme, x.as_of_date)):
        lines.append(
            f"| {r.cohort_scheme} | {r.as_of_date:%Y-%m-%d} | {r.days_held} | "
            f"{r.n_priced}/{r.n_picks} | {_pct(r.cohort_return)} | "
            f"{_pct(r.benchmark_return)} | {_pct(r.excess_return)} |"
        )

    # Per-scheme aggregate excess (simple average across that scheme's cohorts
    # with a computable excess) — a coarse, evidence-light early signal.
    lines.extend(["", "## Aggregate excess vs SPY (simple average across cohorts)", ""])
    by_scheme: dict[str, list[float]] = {}
    for r in results:
        if r.excess_return is not None:
            by_scheme.setdefault(r.cohort_scheme, []).append(r.excess_return)
    if by_scheme:
        for scheme in sorted(by_scheme):
            xs = by_scheme[scheme]
            avg = sum(xs) / len(xs)
            lines.append(f"- **{scheme}**: {_pct(avg)} over {len(xs)} cohort(s)")
    else:
        lines.append("- No cohort has a computable excess yet.")
    lines.append("")
    return "\n".join(lines)


def _write_csv(path: Path, results: list[CohortReturn]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "cohort_scheme": r.cohort_scheme,
                    "as_of_date": r.as_of_date,
                    "eval_date": r.eval_date,
                    "days_held": r.days_held,
                    "n_picks": r.n_picks,
                    "n_priced": r.n_priced,
                    "cohort_return": r.cohort_return,
                    "benchmark_return": r.benchmark_return,
                    "excess_return": r.excess_return,
                }
            )

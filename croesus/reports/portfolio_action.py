from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import duckdb

from croesus.portfolio.actions import ProposedAction
from croesus.portfolio.repository import PortfolioRepository


def write_portfolio_action_reports(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    *,
    reports_dir: str | Path = "reports",
) -> tuple[Path, Path]:
    """Write Markdown and CSV views from persisted rebalance proposal state."""
    repo = PortfolioRepository(conn)
    run = repo.get_rebalance_run(run_id)
    if run is None:
        raise ValueError(f"rebalance run not found: {run_id}")

    output_dir = Path(reports_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    as_of = run["date"]
    markdown_path = output_dir / f"portfolio_action_{as_of:%Y-%m-%d}.md"
    csv_path = output_dir / f"portfolio_action_{as_of:%Y-%m-%d}.csv"

    markdown_path.write_text(_render_markdown(run), encoding="utf-8")
    _write_csv(csv_path, run["actions"])
    return markdown_path, csv_path


def _render_markdown(run: dict[str, Any]) -> str:
    actions: list[ProposedAction] = run["actions"]
    proposed = [a for a in actions if a.action_type in {"trim", "add", "rebalance_to_band", "raise_cash"}]
    blocked = [a for a in actions if a.action_type == "block_new_buy"]
    watch = [a for a in actions if a.action_type == "watch"]
    issues = [a for a in actions if a.action_type != "hold"]
    metadata = run.get("metadata") or {}

    lines = [
        f"# Portfolio Action Report - {run['date']:%Y-%m-%d}",
        "",
        "## Summary",
        f"- Portfolio: {run['portfolio_id']}",
        f"- Profile: {run['profile_id']}",
        f"- Macro posture: {run.get('macro_positioning') or 'Not available'}",
        f"- Decision: {run.get('decision') or 'unknown'}",
        "",
        "## Current Issues",
    ]
    if issues:
        lines.extend(f"- {_reason_summary(action)}" for action in issues)
    else:
        lines.append("- No current policy or concentration issues were detected.")

    lines.extend(["", "## Proposed Actions"])
    if proposed:
        lines.extend(
            f"{index}. {action.human_readable_reason}"
            for index, action in enumerate(proposed, start=1)
        )
    else:
        lines.append("- No trade action is proposed.")

    lines.extend(["", "## Blocked Actions"])
    if blocked:
        lines.extend(f"- {action.human_readable_reason}" for action in blocked)
    else:
        lines.append("- No blocked actions.")

    lines.extend(["", "## Watchlist"])
    if watch:
        lines.extend(f"- {action.human_readable_reason}" for action in watch)
    else:
        lines.append("- No watchlist candidates.")

    lines.extend(["", "## Why"])
    if actions:
        codes = sorted({code for action in actions for code in action.reason_codes})
        lines.append("- Reason codes: " + ", ".join(codes))
    else:
        lines.append("- No actions were generated.")

    lines.extend(
        [
            "",
            "## Data Used",
            f"- latest MacroState date: {metadata.get('latest_macro_state_date') or 'Not available'}",
            f"- latest portfolio snapshot date: {metadata.get('latest_portfolio_snapshot_date') or 'Not available'}",
            f"- latest screening run ID: {metadata.get('latest_screening_run_id') or 'Not available'}",
            "",
        ]
    )
    return "\n".join(lines)


def _write_csv(path: Path, actions: list[ProposedAction]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "action_id",
                "run_id",
                "asset_id",
                "sleeve_name",
                "action_type",
                "current_weight",
                "target_weight",
                "proposed_weight",
                "estimated_trade_value",
                "reason_codes",
                "human_readable_reason",
                "requires_research",
                "requires_user_approval",
            ],
        )
        writer.writeheader()
        for action in actions:
            writer.writerow(
                {
                    "action_id": action.action_id,
                    "run_id": action.run_id,
                    "asset_id": action.asset_id,
                    "sleeve_name": action.sleeve_name,
                    "action_type": action.action_type,
                    "current_weight": action.current_weight,
                    "target_weight": action.target_weight,
                    "proposed_weight": action.proposed_weight,
                    "estimated_trade_value": action.estimated_trade_value,
                    "reason_codes": "|".join(action.reason_codes),
                    "human_readable_reason": action.human_readable_reason,
                    "requires_research": action.requires_research,
                    "requires_user_approval": action.requires_user_approval,
                }
            )


def _reason_summary(action: ProposedAction) -> str:
    target = action.asset_id or action.sleeve_name or action.action_type
    return f"{target}: {', '.join(action.reason_codes)}"

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import duckdb

from croesus.screening.models import ScreeningCandidate, ScreeningRunResult
from croesus.screening.sector_theme import compute_sector_theme_scores


def save_report(
    conn: duckdb.DuckDBPyConnection,
    result: ScreeningRunResult,
    *,
    reports_dir: str | Path = "reports",
    portfolio_id: str | None = None,
) -> tuple[Path, Path]:
    reports_dir = Path(reports_dir)
    report_dir = reports_dir / "screening" / result.as_of_date.isoformat()
    report_dir.mkdir(parents=True, exist_ok=True)

    md_path = report_dir / f"{result.run_id}.md"
    csv_path = report_dir / f"{result.run_id}.csv"

    assets = _load_assets(conn, result)
    sector_theme_scores = compute_sector_theme_scores(
        conn,
        result.run_id,
        portfolio_id=portfolio_id,
        as_of_date=result.as_of_date,
    )
    md_path.write_text(
        generate_markdown(result, assets=assets, sector_theme_scores=sector_theme_scores),
        encoding="utf-8",
    )
    _write_csv(csv_path, result, assets)
    return md_path, csv_path


def generate_markdown(
    result: ScreeningRunResult,
    *,
    assets: dict[str, dict[str, Any]],
    sector_theme_scores: list[Any],
) -> str:
    params = result.screening_params
    weights = params.get("factor_weights", {})
    candidates = [candidate for candidate in result.candidates if candidate.decision_bucket != "skipped"]
    skipped = [*result.skipped, *[candidate for candidate in result.candidates if candidate.decision_bucket == "skipped"]]

    lines: list[str] = [
        f"# Screening Report - {result.as_of_date.isoformat()}",
        "",
        "## Run Summary",
        "",
        f"- Run ID: `{result.run_id}`",
        f"- Ranked assets: {len(candidates)}",
        f"- Skipped assets: {len(skipped)}",
        f"- Regime: {params.get('regime') or 'Neutral fallback'}",
        f"- Positioning: {params.get('positioning') or 'Neutral fallback'}",
        f"- Candidate count: {params.get('candidate_count')}",
        "",
        "## Factor Weights",
        "",
    ]
    for name, value in weights.items():
        lines.append(f"- `{name}`: {_fmt(value)}")

    lines += [
        "",
        "## Top Candidates",
        "",
        "| Rank | Symbol | Asset | Score | Bucket | Portfolio Fit |",
        "|---:|---|---|---:|---|---|",
    ]
    for candidate in candidates:
        asset = assets.get(candidate.asset_id, {})
        lines.append(
            "| "
            f"{candidate.rank or ''} | "
            f"{asset.get('symbol', candidate.asset_id)} | "
            f"{candidate.asset_id} | "
            f"{_fmt(candidate.score)} | "
            f"{candidate.decision_bucket} | "
            f"{candidate.metadata.get('portfolio_fit', '')} |"
        )

    lines += [
        "",
        "## Why The Top Candidates Ranked Here",
        "",
    ]
    for candidate in candidates[:10]:
        lines.append(f"- {_candidate_explanation(candidate, assets.get(candidate.asset_id, {}), weights)}")

    lines += [
        "",
        "## Factor Breakdown",
        "",
        "| Rank | Symbol | Momentum | Liquidity | Trend | Volatility Penalty |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for candidate in candidates:
        asset = assets.get(candidate.asset_id, {})
        fs = candidate.factor_scores
        lines.append(
            "| "
            f"{candidate.rank or ''} | "
            f"{asset.get('symbol', candidate.asset_id)} | "
            f"{_fmt(fs.get('momentum_score'))} | "
            f"{_fmt(fs.get('liquidity_score'))} | "
            f"{_fmt(fs.get('trend_score'))} | "
            f"{_fmt(fs.get('volatility_penalty'))} |"
        )

    _append_scores(lines, "Sector Scores", sector_theme_scores, "sector")
    _append_scores(lines, "Theme Scores", sector_theme_scores, "theme")

    lines += [
        "",
        "## Skipped And Blocked",
        "",
    ]
    blocked = [candidate for candidate in candidates if candidate.decision_bucket == "blocked_by_portfolio_fit"]
    if not skipped and not blocked:
        lines.append("_No skipped or blocked assets._")
    for candidate in blocked:
        asset = assets.get(candidate.asset_id, {})
        lines.append(
            f"- {asset.get('symbol', candidate.asset_id)} blocked: "
            f"{candidate.reason}; exposures={candidate.metadata.get('blocking_exposures', [])}"
        )
    for candidate in skipped:
        asset = assets.get(candidate.asset_id, {})
        lines.append(f"- {asset.get('symbol', candidate.asset_id)} skipped: {candidate.reason}")
    lines.append("")

    return "\n".join(lines)


def _candidate_explanation(
    candidate: ScreeningCandidate,
    asset: dict[str, Any],
    weights: dict[str, float],
) -> str:
    symbol = asset.get("symbol", candidate.asset_id)
    rank = candidate.rank or "-"
    fs = candidate.factor_scores
    momentum = (weights.get("momentum", 0.0), fs.get("momentum_score"))
    liquidity = (weights.get("liquidity", 0.0), fs.get("liquidity_score"))
    trend = (weights.get("trend", 0.0), fs.get("trend_score"))
    volatility = (weights.get("volatility_penalty", 0.0), fs.get("volatility_penalty"))

    contributions = [
        ("momentum", _product(*momentum)),
        ("liquidity", _product(*liquidity)),
        ("trend", _product(*trend)),
    ]
    best_name, best_value = max(contributions, key=lambda item: item[1])
    penalty = _product(*volatility)
    explanation = (
        f"{symbol} ranked #{rank} because {best_name} contributed {_fmt(best_value)} "
        f"to the macro-adjusted score"
    )
    if penalty > 0:
        explanation += f", while volatility penalty subtracted {_fmt(penalty)}"
    else:
        explanation += ", with no volatility penalty drag"
    if candidate.metadata.get("portfolio_fit") == "blocked":
        explanation += f". It is not addable because {candidate.metadata.get('blocking_exposures', [])} is already over limit"
    else:
        explanation += f". Portfolio fit is {candidate.metadata.get('portfolio_fit', 'unknown')}"
    return explanation + "."


def _append_scores(lines: list[str], title: str, scores: list[Any], exposure_type: str) -> None:
    filtered = [score for score in scores if score.exposure_type == exposure_type]
    lines += [
        "",
        f"## {title}",
        "",
    ]
    if not filtered:
        lines.append("_No scores._")
        return
    lines += [
        "| Name | Score | Assets | Current Weight | Limit | Overexposed |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for score in sorted(filtered, key=lambda item: (-item.score, item.exposure_name)):
        lines.append(
            "| "
            f"{score.exposure_name} | "
            f"{_fmt(score.score)} | "
            f"{score.asset_count} | "
            f"{_fmt(score.current_weight)} | "
            f"{_fmt(score.limit_weight)} | "
            f"{score.is_overexposed} |"
        )


def _write_csv(
    path: Path,
    result: ScreeningRunResult,
    assets: dict[str, dict[str, Any]],
) -> None:
    fieldnames = [
        "symbol",
        "asset_id",
        "rank",
        "score",
        "decision_bucket",
        "reason",
        "portfolio_fit",
        "sector",
        "industry",
        "theme_tags",
        "momentum_score",
        "liquidity_score",
        "trend_score",
        "volatility_penalty",
    ]
    candidates = [*result.candidates, *result.skipped]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in candidates:
            asset = assets.get(candidate.asset_id, {})
            fs = candidate.factor_scores
            writer.writerow(
                {
                    "symbol": asset.get("symbol", candidate.asset_id),
                    "asset_id": candidate.asset_id,
                    "rank": candidate.rank,
                    "score": "" if candidate.score is None else candidate.score,
                    "decision_bucket": candidate.decision_bucket,
                    "reason": candidate.reason,
                    "portfolio_fit": candidate.metadata.get("portfolio_fit", ""),
                    "sector": asset.get("sector", ""),
                    "industry": asset.get("industry", ""),
                    "theme_tags": ";".join(asset.get("theme_tags", [])),
                    "momentum_score": fs.get("momentum_score"),
                    "liquidity_score": fs.get("liquidity_score"),
                    "trend_score": fs.get("trend_score"),
                    "volatility_penalty": fs.get("volatility_penalty"),
                }
            )


def _load_assets(
    conn: duckdb.DuckDBPyConnection,
    result: ScreeningRunResult,
) -> dict[str, dict[str, Any]]:
    asset_ids = sorted({candidate.asset_id for candidate in [*result.candidates, *result.skipped]})
    if not asset_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT asset_id, symbol, sector, industry, metadata
        FROM assets
        WHERE asset_id IN ({", ".join("?" for _ in asset_ids)})
        """,
        asset_ids,
    ).fetchall()
    assets: dict[str, dict[str, Any]] = {}
    for asset_id, symbol, sector, industry, metadata in rows:
        metadata_dict = _loads(metadata)
        tags = metadata_dict.get("theme_tags", []) if isinstance(metadata_dict, dict) else []
        assets[asset_id] = {
            "symbol": symbol,
            "sector": sector,
            "industry": industry,
            "theme_tags": tags if isinstance(tags, list) else [],
        }
    return assets


def _loads(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _product(weight: float, score: float | None) -> float:
    return weight * (score or 0.0)


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)

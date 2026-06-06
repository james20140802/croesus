from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from typing import Any

import duckdb

from croesus.screening.models import SectorThemeScore


def compute_sector_theme_scores(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    *,
    portfolio_id: str | None = None,
    as_of_date: date | None = None,
) -> list[SectorThemeScore]:
    rows = conn.execute(
        """
        SELECT sr.asset_id, sr.score, a.sector, a.industry, a.metadata
        FROM screening_results sr
        JOIN assets a ON a.asset_id = sr.asset_id
        WHERE sr.run_id = ?
          AND sr.score IS NOT NULL
          AND sr.decision_bucket <> 'skipped'
        """,
        [run_id],
    ).fetchall()

    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for _asset_id, score, sector, industry, metadata in rows:
        if sector:
            buckets[("sector", sector)].append(float(score))
        if industry:
            buckets[("industry", industry)].append(float(score))
        for tag in _theme_tags(metadata):
            buckets[("theme", tag)].append(float(score))

    overlay = _load_exposure_overlay(conn, portfolio_id, as_of_date)
    scores: list[SectorThemeScore] = []
    for (exposure_type, exposure_name), values in sorted(buckets.items()):
        exposure = overlay.get((exposure_type, exposure_name), {})
        scores.append(
            SectorThemeScore(
                exposure_type=exposure_type,
                exposure_name=exposure_name,
                score=sum(values) / len(values),
                asset_count=len(values),
                current_weight=exposure.get("weight"),
                limit_weight=exposure.get("limit_weight"),
                is_overexposed=bool(exposure.get("is_violation", False)),
            )
        )
    return scores


def _load_exposure_overlay(
    conn: duckdb.DuckDBPyConnection,
    portfolio_id: str | None,
    as_of_date: date | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    if portfolio_id is None or as_of_date is None:
        return {}
    rows = conn.execute(
        """
        WITH latest AS (
          SELECT max(as_of_date) AS latest_date
          FROM portfolio_exposures
          WHERE portfolio_id = ? AND as_of_date <= ?
        )
        SELECT exposure_type, exposure_name, weight, limit_weight, is_violation
        FROM portfolio_exposures, latest
        WHERE portfolio_id = ?
          AND as_of_date = latest.latest_date
        """,
        [portfolio_id, as_of_date, portfolio_id],
    ).fetchall()
    return {
        (exposure_type, exposure_name): {
            "weight": weight,
            "limit_weight": limit_weight,
            "is_violation": bool(is_violation),
        }
        for exposure_type, exposure_name, weight, limit_weight, is_violation in rows
    }


def _theme_tags(metadata: Any) -> list[str]:
    if metadata is None:
        return []
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    tags = metadata.get("theme_tags", []) if isinstance(metadata, dict) else []
    return tags if isinstance(tags, list) else []

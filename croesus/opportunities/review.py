from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from croesus.factors.equity.repository import ValuationSnapshotRepository
from croesus.opportunities.selection import (
    OpportunityMethodology,
    select_methodology,
)
from croesus.research.thesis_repository import ThesisGradeRepository


SCENARIOS = ("bear", "base", "bull")


@dataclass(frozen=True)
class OpportunityCard:
    asset_id: str
    symbol: str
    name: str | None
    methodology_key: str
    as_of_date: date
    current_price: float | None
    mechanical_intrinsic_value: float | None
    mechanical_upside_pct: float | None
    band_intrinsic_by_scenario: dict[str, float | None]
    band_upside_by_scenario: dict[str, float | None]
    base_upside_pct: float | None
    thesis_as_of_date: date | None
    thesis_confidence: str | None
    evidence_source: str | None
    moat_grade: str | None
    tech_grade: str | None
    sector_grade: str | None
    disruption_grade: str | None
    moat_evidence: str | None
    tech_evidence: str | None
    sector_evidence: str | None
    disruption_evidence: str | None
    bear_case: str | None


@dataclass(frozen=True)
class OpportunityReviewResult:
    methodology: OpportunityMethodology
    as_of_date: date
    cards: list[OpportunityCard]
    recommendation_only: bool = True


def _asset_labels(conn: duckdb.DuckDBPyConnection, asset_ids: list[str]) -> dict[str, tuple[str, str | None]]:
    if not asset_ids:
        return {}
    placeholders = ", ".join(["?"] * len(asset_ids))
    rows = conn.execute(
        f"SELECT asset_id, symbol, name FROM assets WHERE asset_id IN ({placeholders})",
        asset_ids,
    ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def _latest_band_rows(conn: duckdb.DuckDBPyConnection, as_of: date) -> dict[str, dict[str, tuple]]:
    rows = conn.execute(
        """
        WITH complete_dates AS (
            SELECT asset_id, date
            FROM intrinsic_value_bands
            WHERE date <= ?
              AND scenario IN ('bear', 'base', 'bull')
            GROUP BY asset_id, date
            HAVING COUNT(DISTINCT scenario) = 3
        ),
        latest_complete AS (
            SELECT
                asset_id,
                date,
                ROW_NUMBER() OVER (PARTITION BY asset_id ORDER BY date DESC) AS rn
            FROM complete_dates
        )
        SELECT
            bands.asset_id,
            bands.date,
            bands.scenario,
            bands.intrinsic_value_per_share,
            bands.current_price,
            bands.upside_pct
        FROM intrinsic_value_bands AS bands
        JOIN latest_complete AS latest
          ON bands.asset_id = latest.asset_id
         AND bands.date = latest.date
        WHERE latest.rn = 1
          AND bands.scenario IN ('bear', 'base', 'bull')
        ORDER BY bands.asset_id, bands.scenario
        """,
        [as_of],
    ).fetchall()
    grouped: dict[str, dict[str, tuple]] = {}
    for row in rows:
        grouped.setdefault(row[0], {})[row[2]] = row
    return grouped


def _opportunity_card_sort_key(card: OpportunityCard) -> tuple[int, float, str]:
    if card.base_upside_pct is None:
        return (1, 0.0, card.symbol)
    return (0, -card.base_upside_pct, card.symbol)


def _review_methodology_a(
    conn: duckdb.DuckDBPyConnection,
    *,
    methodology: OpportunityMethodology,
    as_of: date,
    limit: int,
) -> list[OpportunityCard]:
    # _latest_band_rows only returns assets whose latest complete date carries
    # all three scenarios, so every grouped entry has bear/base/bull present.
    band_rows = _latest_band_rows(conn, as_of)
    asset_ids = list(band_rows)
    labels = _asset_labels(conn, asset_ids)
    valuation_repo = ValuationSnapshotRepository(conn)
    thesis_repo = ThesisGradeRepository(conn)

    cards: list[OpportunityCard] = []
    for asset_id in asset_ids:
        scenarios = band_rows[asset_id]
        base = scenarios["base"]
        band_date = base[1]
        band_intrinsic = {scenario: scenarios[scenario][3] for scenario in SCENARIOS}
        band_upside = {scenario: scenarios[scenario][5] for scenario in SCENARIOS}
        # Load valuation/thesis as of the band's own date (not the review
        # date) so the rendered grades match the thesis the band was built from.
        valuation = valuation_repo.get(asset_id, band_date)
        thesis = thesis_repo.load_latest_for_asset(asset_id, band_date)
        symbol, name = labels.get(asset_id, (asset_id, None))
        cards.append(
            OpportunityCard(
                asset_id=asset_id,
                symbol=symbol,
                name=name,
                methodology_key=methodology.key,
                as_of_date=base[1],
                current_price=base[4],
                mechanical_intrinsic_value=(
                    valuation.intrinsic_value_per_share if valuation else None
                ),
                mechanical_upside_pct=valuation.upside_pct if valuation else None,
                band_intrinsic_by_scenario=band_intrinsic,
                band_upside_by_scenario=band_upside,
                base_upside_pct=band_upside["base"],
                thesis_as_of_date=thesis.as_of_date if thesis else None,
                thesis_confidence=thesis.confidence if thesis else None,
                evidence_source=thesis.evidence_source if thesis else None,
                moat_grade=thesis.moat_grade if thesis else None,
                tech_grade=thesis.tech_grade if thesis else None,
                sector_grade=thesis.sector_grade if thesis else None,
                disruption_grade=thesis.disruption_grade if thesis else None,
                moat_evidence=thesis.moat_evidence if thesis else None,
                tech_evidence=thesis.tech_evidence if thesis else None,
                sector_evidence=thesis.sector_evidence if thesis else None,
                disruption_evidence=thesis.disruption_evidence if thesis else None,
                bear_case=thesis.bear_case if thesis else None,
            )
        )

    cards.sort(key=_opportunity_card_sort_key)
    return cards[:limit]


def run_opportunity_review(
    conn: duckdb.DuckDBPyConnection,
    *,
    methodology_key: str | None = None,
    methodology: OpportunityMethodology | None = None,
    as_of_date: date | None = None,
    limit: int = 20,
) -> OpportunityReviewResult:
    # Callers that already resolved the methodology (e.g. the CLI menu) pass it
    # in directly to avoid re-running selection; otherwise resolve from the key.
    if methodology is None:
        methodology = select_methodology(methodology_key)
    as_of = as_of_date or date.today()
    if methodology.key == "moat_adjusted_intrinsic_value":
        cards = _review_methodology_a(
            conn, methodology=methodology, as_of=as_of, limit=limit
        )
    else:  # pragma: no cover - guarded by select_methodology
        cards = []
    return OpportunityReviewResult(
        methodology=methodology,
        as_of_date=as_of,
        cards=cards,
    )

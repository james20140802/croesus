from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

import duckdb

from croesus.factors.equity.normalized import QUALITY_OK
from croesus.factors.equity.normalized_repository import NormalizedDcfRepository
from croesus.factors.equity.repository import ValuationSnapshotRepository
from croesus.opportunities.risk_gate import (
    DEFAULT_MIN_LIQUIDITY_USD,
    RiskGateVerdict,
    evaluate_candidates,
)
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
    normalized_intrinsic_value: float | None = None
    normalized_upside_pct: float | None = None
    reference_growth: float | None = None
    implied_growth: float | None = None
    plausibility_gap: float | None = None
    valuation_quality: str | None = None
    n_fcf_years: int | None = None
    risk_gate: RiskGateVerdict | None = None


@dataclass(frozen=True)
class OpportunityReviewResult:
    methodology: OpportunityMethodology
    as_of_date: date
    cards: list[OpportunityCard]
    recommendation_only: bool = True
    gate_summary: dict[str, int] | None = None


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


def _normalized_card_sort_key(card: OpportunityCard) -> tuple[int, int, float, str]:
    # Tier first by trustworthiness: clean "ok" names rank above flagged ones
    # (reference_unreliable / short_history); gap-less cards rank last. Then
    # ascending plausibility_gap within a tier — cheapest first.
    if card.plausibility_gap is None:
        return (2, 0, 0.0, card.symbol)
    quality_tier = 0 if card.valuation_quality == QUALITY_OK else 1
    return (0, quality_tier, card.plausibility_gap, card.symbol)


def _review_methodology_normalized_dcf(
    conn: duckdb.DuckDBPyConnection,
    *,
    methodology: OpportunityMethodology,
    as_of: date,
    limit: int,
) -> list[OpportunityCard]:
    snapshots = NormalizedDcfRepository(conn).load_latest(as_of)
    labels = _asset_labels(conn, [s.asset_id for s in snapshots])
    cards: list[OpportunityCard] = []
    for snap in snapshots:
        symbol, name = labels.get(snap.asset_id, (snap.asset_id, None))
        cards.append(OpportunityCard(
            asset_id=snap.asset_id, symbol=symbol, name=name,
            methodology_key=methodology.key, as_of_date=snap.date,
            current_price=snap.current_price,
            mechanical_intrinsic_value=None, mechanical_upside_pct=None,
            band_intrinsic_by_scenario={}, band_upside_by_scenario={},
            base_upside_pct=None,
            thesis_as_of_date=None, thesis_confidence=None, evidence_source=None,
            moat_grade=None, tech_grade=None, sector_grade=None,
            disruption_grade=None, moat_evidence=None, tech_evidence=None,
            sector_evidence=None, disruption_evidence=None, bear_case=None,
            normalized_intrinsic_value=snap.normalized_intrinsic_value_per_share,
            normalized_upside_pct=snap.normalized_upside_pct,
            reference_growth=snap.reference_growth,
            implied_growth=snap.implied_growth,
            plausibility_gap=snap.plausibility_gap,
            valuation_quality=snap.valuation_quality,
            n_fcf_years=snap.n_fcf_years,
        ))
    cards.sort(key=_normalized_card_sort_key)
    return cards[:limit]


def run_opportunity_review(
    conn: duckdb.DuckDBPyConnection,
    *,
    methodology_key: str | None = None,
    methodology: OpportunityMethodology | None = None,
    as_of_date: date | None = None,
    limit: int = 20,
    portfolio_id: str = "default",
    profile_id: str = "default",
    apply_risk_gate: bool = True,
    min_liquidity_usd: float | None = DEFAULT_MIN_LIQUIDITY_USD,
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
    elif methodology.key == "normalized_dcf":
        cards = _review_methodology_normalized_dcf(
            conn, methodology=methodology, as_of=as_of, limit=limit
        )
    else:  # pragma: no cover - guarded by select_methodology
        cards = []

    # Phase E: attach a recommendation-only risk-gate verdict per candidate.
    # An unloadable profile yields an empty dict -> cards stay ungated.
    gate_summary: dict[str, int] | None = None
    if apply_risk_gate and cards:
        verdicts = evaluate_candidates(
            conn,
            [card.asset_id for card in cards],
            portfolio_id=portfolio_id,
            profile_id=profile_id,
            as_of_date=as_of,
            min_liquidity_usd=min_liquidity_usd,
        )
        if verdicts:
            cards = [
                replace(card, risk_gate=verdicts.get(card.asset_id))
                for card in cards
            ]
            gate_summary = {"pass": 0, "warn": 0, "block": 0}
            for card in cards:
                if card.risk_gate is not None:
                    gate_summary[card.risk_gate.status] += 1

    return OpportunityReviewResult(
        methodology=methodology,
        as_of_date=as_of,
        cards=cards,
        gate_summary=gate_summary,
    )

from __future__ import annotations
import duckdb

from croesus.web.cache import TTLCache
from croesus.macro._loader import load_latest_macro_state
from croesus.web.viewmodels import MacroView

DEFAULT_PORTFOLIO_ID = "default"
DEFAULT_PROFILE_ID = "default"
opportunity_cache = TTLCache(ttl_seconds=60.0)


def resolve_portfolio_id(conn: duckdb.DuckDBPyConnection) -> str:
    row = conn.execute(
        "SELECT portfolio_id FROM portfolios ORDER BY created_at LIMIT 1"
    ).fetchone()
    return row[0] if row else DEFAULT_PORTFOLIO_ID


def resolve_profile_id(conn: duckdb.DuckDBPyConnection, portfolio_id: str) -> str:
    row = conn.execute(
        "SELECT profile_id FROM portfolios WHERE portfolio_id = ?", [portfolio_id]
    ).fetchone()
    return row[0] if row and row[0] else DEFAULT_PROFILE_ID


def resolve_symbol_map(
    conn: duckdb.DuckDBPyConnection, asset_ids: list[str]
) -> dict[str, tuple[str | None, str | None]]:
    if not asset_ids:
        return {}
    placeholders = ",".join(["?"] * len(asset_ids))
    rows = conn.execute(
        f"SELECT asset_id, symbol, name FROM assets WHERE asset_id IN ({placeholders})",
        asset_ids,
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def build_macro_view(conn) -> MacroView | None:
    state = load_latest_macro_state(conn)
    if state is None:
        return None
    rows = conn.execute(
        "SELECT date, regime, positioning, amplifier_score, confirmation_score "
        "FROM macro_scores ORDER BY date DESC LIMIT 90"
    ).fetchall()
    history = [
        {"date": str(r[0]), "regime": r[1], "positioning": r[2],
         "amplifier_score": r[3], "confirmation_score": r[4]}
        for r in reversed(rows)
    ]
    return MacroView(
        date=state.date, regime=state.regime, positioning=state.positioning,
        regime_confidence=state.regime_confidence, amplifier_score=state.amplifier_score,
        confirmation_score=state.confirmation_score,
        growth_direction=getattr(state, "growth_direction", "") or "",
        inflation_direction=getattr(state, "inflation_direction", "") or "",
        warnings=state.warnings,
        opportunities=state.opportunities, regime_methods=state.regime_methods,
        history=history,
        raw_indicators=getattr(state, "raw_indicators", {}) or {},
    )


from croesus.screening.repository import ScreeningRepository
from croesus.web.viewmodels import ScreeningView, ScreeningRow


def _latest_screening_run_id(conn) -> str | None:
    row = conn.execute(
        "SELECT run_id FROM screening_results GROUP BY run_id ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def build_screening_view(conn, bucket: str | None = None) -> ScreeningView:
    run_id = _latest_screening_run_id(conn)
    if run_id is None:
        return ScreeningView(run_id=None, as_of_date=None, rows=[])
    candidates = ScreeningRepository(conn).list_results(run_id)
    symbols = resolve_symbol_map(conn, [c.asset_id for c in candidates])
    rows = []
    for c in candidates:
        if bucket and c.decision_bucket != bucket:
            continue
        sym, name = symbols.get(c.asset_id, (c.asset_id, None))
        rows.append(ScreeningRow(rank=c.rank, symbol=sym or c.asset_id, name=name,
            score=c.score, decision_bucket=c.decision_bucket, reason=c.reason,
            factor_scores=c.factor_scores, asset_id=c.asset_id))
    as_of = None
    parts = run_id.split("-")
    if len(parts) >= 4:
        from datetime import date as _date
        try:
            as_of = _date.fromisoformat("-".join(parts[1:4]))
        except ValueError:
            as_of = None
    return ScreeningView(run_id=run_id, as_of_date=as_of, rows=rows)


from croesus.portfolio.repository import PortfolioRepository
from croesus.web.viewmodels import PortfolioView
from croesus.web import labels


def _korean_action_reason(action, symbol: str | None) -> str:
    """구조화된 필드(action_type, 사유 코드, 대상)로 한국어 사유 문장을 조립한다.

    백엔드의 영문 human_readable_reason 대신, 사용자가 바로 이해할 수 있는
    '대상 — 행동: 사유' 형태로 보여준다.
    """
    # 행동 종류는 칩으로 따로 보여주므로 사유 문장에서는 중복하지 않는다.
    subject = symbol or labels.sleeve_label(getattr(action, "sleeve_name", None))
    why = labels.reason_codes_label(getattr(action, "reason_codes", None) or [])
    if subject and why:
        return f"{subject} · {why}"
    return why or subject or labels.action_label(action.action_type)


def build_portfolio_view(conn) -> PortfolioView:
    pid = resolve_portfolio_id(conn)
    repo = PortfolioRepository(conn)
    row = conn.execute(
        "SELECT max(as_of_date) FROM portfolio_holdings WHERE portfolio_id = ?", [pid]
    ).fetchone()
    as_of = row[0] if row else None
    if as_of is None:
        return PortfolioView(as_of_date=None, total_market_value=None, unrealized_pnl=None)
    holdings = repo.get_holdings(pid, as_of)
    exposures = repo.get_exposures(pid, as_of)
    drifts = repo.get_drifts(pid, as_of)
    snapshot = repo.get_snapshot(pid, as_of) or {}
    run = repo.load_latest_rebalance_run(pid) or {}
    actions = run.get("actions", [])
    symbols = resolve_symbol_map(conn, [h.asset_id for h in holdings])
    total_mv = snapshot.get("total_market_value")
    h_rows = []
    for h in holdings:
        sym, name = symbols.get(h.asset_id, (h.asset_id, None))
        weight = (h.market_value / total_mv) if (h.market_value and total_mv) else None
        h_rows.append({"symbol": sym or h.asset_id, "name": name, "quantity": h.quantity,
                       "avg_cost": h.avg_cost, "market_value": h.market_value,
                       "currency": h.currency, "weight": weight})
    e_rows = [{"exposure_type": e.exposure_type, "exposure_name": e.exposure_name,
               "weight": e.weight, "limit_weight": e.limit_weight,
               "is_violation": e.is_violation} for e in exposures]
    d_rows = [{"sleeve_name": d.sleeve_name, "current_weight": d.current_weight,
               "target_weight": d.target_weight, "drift": d.drift,
               "is_outside_band": d.is_outside_band} for d in drifts]
    action_assets = resolve_symbol_map(conn, [a.asset_id for a in actions if a.asset_id])
    a_rows = []
    for a in actions:
        sym, _ = action_assets.get(a.asset_id, (a.asset_id, None))
        a_rows.append({
            "action_type": a.action_type,
            "human_readable_reason": a.human_readable_reason,
            "reason_ko": _korean_action_reason(a, sym),
            "reason_codes": a.reason_codes,
            "estimated_trade_value": a.estimated_trade_value,
            "asset_id": a.asset_id, "symbol": sym, "sleeve_name": a.sleeve_name,
        })
    pnl = snapshot.get("unrealized_pnl")
    cost_basis = snapshot.get("total_cost_basis")
    if cost_basis is None and total_mv is not None and pnl is not None:
        cost_basis = total_mv - pnl
    return_pct = (pnl / cost_basis * 100) if (pnl is not None and cost_basis) else None

    base_currency = "USD"
    try:
        p = PortfolioRepository(conn).get_portfolio(pid)
        base_currency = getattr(p, "base_currency", None) or "USD"
    except Exception:
        pass

    history = []
    try:
        hrows = conn.execute(
            "SELECT as_of_date, total_market_value, total_cost_basis, unrealized_pnl "
            "FROM portfolio_snapshots WHERE portfolio_id = ? ORDER BY as_of_date", [pid]
        ).fetchall()
        for r in hrows:
            mv, cb, p_ = r[1], r[2], r[3]
            if cb is None and mv is not None and p_ is not None:
                cb = mv - p_
            history.append({
                "date": str(r[0]), "market_value": mv, "cost_basis": cb,
                "return_pct": (p_ / cb * 100) if (p_ is not None and cb) else None,
            })
    except Exception:
        history = []

    return PortfolioView(as_of_date=as_of, total_market_value=total_mv,
        unrealized_pnl=pnl, cost_basis=cost_basis, return_pct=return_pct,
        base_currency=base_currency, holdings=h_rows,
        exposures=e_rows, drifts=d_rows, actions=a_rows, history=history)


from croesus.opportunities.review import run_opportunity_review
from croesus.web.viewmodels import OpportunityView, OpportunityRow

_OPP_METHODOLOGY = "moat_adjusted_intrinsic_value"


NORMALIZED_METHODOLOGY = "normalized_dcf"
# Methodologies offered in the web opportunities toggle (key, label).
OPP_METHODOLOGY_CHOICES = (
    (_OPP_METHODOLOGY, "적정가 밴드"),
    (NORMALIZED_METHODOLOGY, "정규화 reverse-DCF"),
)


def _resolve_opp_methodology(methodology: str | None) -> str:
    keys = {k for k, _ in OPP_METHODOLOGY_CHOICES}
    return methodology if methodology in keys else _OPP_METHODOLOGY


def _card_to_row(card) -> OpportunityRow:
    gate = card.risk_gate  # Phase E: RiskGateVerdict | None
    return OpportunityRow(
        asset_id=card.asset_id, symbol=card.symbol, name=card.name,
        current_price=card.current_price, base_upside_pct=card.base_upside_pct,
        bands=card.band_intrinsic_by_scenario,
        grades={"moat": card.moat_grade, "tech": card.tech_grade,
                "sector": card.sector_grade, "disruption": card.disruption_grade},
        confidence=card.thesis_confidence,
        gate_status=(gate.status if gate else None),
        gate_reason_codes=(list(gate.reason_codes) if gate else []),
        gate_notes=(list(gate.notes) if gate else []),
        methodology_key=card.methodology_key,
        plausibility_gap=card.plausibility_gap,
        implied_growth=card.implied_growth,
        reference_growth=card.reference_growth,
        normalized_upside_pct=card.normalized_upside_pct,
        valuation_quality=card.valuation_quality)


def build_opportunity_view(
    conn, gate: str | None = None, methodology: str | None = None
) -> OpportunityView:
    pid = resolve_portfolio_id(conn)
    profile_id = resolve_profile_id(conn, pid)
    method = _resolve_opp_methodology(methodology)

    def factory():
        result = run_opportunity_review(
            conn, methodology_key=method,
            portfolio_id=pid, profile_id=profile_id, apply_risk_gate=True)
        return OpportunityView(
            as_of_date=result.as_of_date,
            rows=[_card_to_row(c) for c in result.cards],
            gate_summary=getattr(result, "gate_summary", None))
    view = opportunity_cache.get_or_set((method, pid, "view"), factory)
    if gate:  # 게이트 상태 필터(캐시된 전체에서 파생)
        rows = [r for r in view.rows if r.gate_status == gate]
        return OpportunityView(as_of_date=view.as_of_date, rows=rows,
                               gate_summary=view.gate_summary)
    return view


def build_opportunity_detail(conn, asset_id: str, methodology: str | None = None):
    view = build_opportunity_view(conn, methodology=methodology)
    for row in view.rows:
        if row.asset_id == asset_id:
            return row
    return None


from croesus.web.viewmodels import AssetDetailView


def _parse_json(value):
    import json
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def build_asset_detail(conn, asset_id: str, *, history_days: int = 180) -> AssetDetailView | None:
    """한 종목의 상세: 가격 추이 + 스크리닝 점수 근거 + LLM 정성 평가."""
    arow = conn.execute(
        "SELECT symbol, name FROM assets WHERE asset_id = ?", [asset_id]
    ).fetchone()
    if arow is None:
        return None
    symbol, name = arow[0], arow[1]

    price_rows = conn.execute(
        "SELECT date, close FROM prices_daily WHERE asset_id = ? "
        "ORDER BY date DESC LIMIT ?", [asset_id, history_days]
    ).fetchall()
    price_history = [{"date": str(r[0]), "close": r[1]} for r in reversed(price_rows)]
    current_price = price_history[-1]["close"] if price_history else None

    screening = None
    srow = conn.execute(
        "SELECT run_id, score, rank, decision_bucket, reason, reason_codes, factor_scores "
        "FROM screening_results WHERE asset_id = ? ORDER BY run_id DESC LIMIT 1",
        [asset_id],
    ).fetchone()
    if srow is not None:
        screening = {
            "run_id": srow[0], "score": srow[1], "rank": srow[2],
            "decision_bucket": srow[3], "reason": srow[4],
            "reason_codes": _parse_json(srow[5]) or [],
            "factor_scores": _parse_json(srow[6]) or {},
        }

    raw_factors = {}
    try:
        frows = conn.execute(
            "SELECT fv.factor_name, fv.value FROM factor_values fv "
            "JOIN (SELECT factor_name, MAX(date) md FROM factor_values "
            "      WHERE asset_id = ? GROUP BY factor_name) m "
            "  ON fv.factor_name = m.factor_name AND fv.date = m.md "
            "WHERE fv.asset_id = ?", [asset_id, asset_id]
        ).fetchall()
        raw_factors = {r[0]: r[1] for r in frows}
    except Exception:
        raw_factors = {}

    thesis = None
    try:
        trow = conn.execute(
            "SELECT moat_grade, moat_evidence, tech_grade, tech_evidence, "
            "sector_grade, sector_evidence, disruption_grade, disruption_evidence, "
            "bear_case, confidence, evidence_source, as_of_date "
            "FROM thesis_grades WHERE asset_id = ? AND status = 'generated' "
            "ORDER BY as_of_date DESC LIMIT 1", [asset_id]
        ).fetchone()
        if trow is not None:
            thesis = {
                "moat_grade": trow[0], "moat_evidence": trow[1],
                "tech_grade": trow[2], "tech_evidence": trow[3],
                "sector_grade": trow[4], "sector_evidence": trow[5],
                "disruption_grade": trow[6], "disruption_evidence": trow[7],
                "bear_case": trow[8], "confidence": trow[9],
                "evidence_source": trow[10], "as_of_date": str(trow[11]),
            }
    except Exception:  # thesis_grades 테이블이 없거나 비어 있어도 무해
        thesis = None

    return AssetDetailView(
        asset_id=asset_id, symbol=symbol or asset_id, name=name,
        current_price=current_price, price_history=price_history,
        screening=screening, raw_factors=raw_factors, thesis=thesis)


from croesus.web.viewmodels import HomeView, Badge


def build_home_view(conn) -> HomeView:
    macro = build_macro_view(conn)
    portfolio = build_portfolio_view(conn)
    opps = build_opportunity_view(conn)
    screening = build_screening_view(conn)
    macro_badge = (
        Badge("시장 자세",
              f"{labels.regime_label(macro.regime)} · {labels.positioning_label(macro.positioning)}",
              labels.positioning_tone(macro.positioning))
        if macro else None)
    drift_alerts = [f"{labels.sleeve_label(d['sleeve_name'])} 비중이 목표 범위를 벗어났습니다"
                    for d in portfolio.drifts if d.get("is_outside_band")]
    drift_alerts += [f"{labels.exposure_type_label(e.get('exposure_type'))} '{e.get('exposure_name', '')}' 비중이 한도를 넘었습니다"
                     for e in portfolio.exposures if e.get("is_violation")]
    freshness = []
    if macro and macro.date:
        freshness.append(Badge("매크로", str(macro.date), "ok"))
    if portfolio.as_of_date:
        freshness.append(Badge("포트폴리오", str(portfolio.as_of_date), "ok"))
    if screening.as_of_date:
        freshness.append(Badge("스크리닝", str(screening.as_of_date), "ok"))
    portfolio_summary = None
    if portfolio.as_of_date:
        top = sorted(
            [h for h in portfolio.holdings if h.get("weight") is not None],
            key=lambda h: h["weight"], reverse=True,
        )[:4]
        portfolio_summary = {
            "value": portfolio.total_market_value,
            "pnl": portfolio.unrealized_pnl,
            "return_pct": portfolio.return_pct,
            "currency": portfolio.base_currency,
            "as_of": portfolio.as_of_date,
            "top_holdings": [{"symbol": h["symbol"], "weight": h["weight"]} for h in top],
            "holding_count": len(portfolio.holdings),
        }
    return HomeView(macro=macro_badge, actions=portfolio.actions[:3],
        action_count=len(portfolio.actions), opportunity_count=len(opps.rows),
        drift_alerts=drift_alerts, screening_count=len(screening.rows), freshness=freshness,
        macro_detail=macro, portfolio=portfolio_summary)

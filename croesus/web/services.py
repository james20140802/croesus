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
            factor_scores=c.factor_scores))
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
    return PortfolioView(as_of_date=as_of, total_market_value=total_mv,
        unrealized_pnl=snapshot.get("unrealized_pnl"), holdings=h_rows,
        exposures=e_rows, drifts=d_rows, actions=a_rows)


from croesus.opportunities.review import run_opportunity_review
from croesus.web.viewmodels import OpportunityView, OpportunityRow

_OPP_METHODOLOGY = "moat_adjusted_intrinsic_value"


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
        gate_notes=(list(gate.notes) if gate else []))


def build_opportunity_view(conn, gate: str | None = None) -> OpportunityView:
    pid = resolve_portfolio_id(conn)
    profile_id = resolve_profile_id(conn, pid)

    def factory():
        result = run_opportunity_review(
            conn, methodology_key=_OPP_METHODOLOGY,
            portfolio_id=pid, profile_id=profile_id, apply_risk_gate=True)
        return OpportunityView(
            as_of_date=result.as_of_date,
            rows=[_card_to_row(c) for c in result.cards],
            gate_summary=getattr(result, "gate_summary", None))
    view = opportunity_cache.get_or_set((_OPP_METHODOLOGY, pid, "view"), factory)
    if gate:  # 게이트 상태 필터(캐시된 전체에서 파생)
        rows = [r for r in view.rows if r.gate_status == gate]
        return OpportunityView(as_of_date=view.as_of_date, rows=rows,
                               gate_summary=view.gate_summary)
    return view


def build_opportunity_detail(conn, asset_id: str):
    view = build_opportunity_view(conn)
    for row in view.rows:
        if row.asset_id == asset_id:
            return row
    return None


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
    drift_alerts += [f"{labels.exposure_type_label(e['exposure_type'])} '{e['exposure_name']}' 비중이 한도를 넘었습니다"
                     for e in portfolio.exposures if e.get("is_violation")]
    freshness = []
    if macro and macro.date:
        freshness.append(Badge("매크로", str(macro.date), "ok"))
    if portfolio.as_of_date:
        freshness.append(Badge("포트폴리오", str(portfolio.as_of_date), "ok"))
    if screening.as_of_date:
        freshness.append(Badge("스크리닝", str(screening.as_of_date), "ok"))
    return HomeView(macro=macro_badge, actions=portfolio.actions[:3],
        action_count=len(portfolio.actions), opportunity_count=len(opps.rows),
        drift_alerts=drift_alerts, screening_count=len(screening.rows), freshness=freshness,
        macro_detail=macro)

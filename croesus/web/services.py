from __future__ import annotations
import duckdb

from croesus.web.cache import TTLCache
from croesus.macro._loader import load_latest_macro_state
from croesus.web.viewmodels import MacroView

DEFAULT_PORTFOLIO_ID = "default"
opportunity_cache = TTLCache(ttl_seconds=60.0)


def resolve_portfolio_id(conn: duckdb.DuckDBPyConnection) -> str:
    row = conn.execute(
        "SELECT portfolio_id FROM portfolios ORDER BY created_at LIMIT 1"
    ).fetchone()
    return row[0] if row else DEFAULT_PORTFOLIO_ID


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
        confirmation_score=state.confirmation_score, warnings=state.warnings,
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

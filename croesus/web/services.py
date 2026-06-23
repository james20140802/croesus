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

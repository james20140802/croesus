from __future__ import annotations
import json
import tempfile
from pathlib import Path
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection, get_write_connection
from croesus.web.forms import holdings_form_to_csv
from croesus.web.services import build_portfolio_view, resolve_portfolio_id, opportunity_cache
from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot

router = APIRouter()


@router.get("/portfolio", response_class=HTMLResponse)
def portfolio(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_portfolio_view(conn)
    donut = json.dumps([{"name": h["symbol"], "value": h["market_value"] or 0}
                        for h in view.holdings])
    return templates.TemplateResponse(request, "portfolio.html",
        {"title": "포트폴리오", "view": view, "donut_json": donut})


@router.get("/portfolio/edit", response_class=HTMLResponse)
def edit_holdings(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_portfolio_view(conn)
    return templates.TemplateResponse(request, "portfolio_edit.html",
        {"title": "보유 편집", "view": view})


@router.post("/portfolio/holdings", response_class=HTMLResponse)
async def save_holdings(request: Request, db_path=Depends(get_db_path)):
    form = await request.form()
    data = {k: form.getlist(k) for k in ("symbol", "quantity", "avg_cost", "currency", "market_value")}
    csv_text = holdings_form_to_csv(data)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
        tmp.write(csv_text)
        tmp_path = Path(tmp.name)
    try:
        with get_write_connection(db_path) as conn:
            pid = resolve_portfolio_id(conn)
            run_portfolio_snapshot(conn, tmp_path, portfolio_id=pid)
    finally:
        tmp_path.unlink(missing_ok=True)
    opportunity_cache.invalidate()
    return RedirectResponse("/portfolio", status_code=303)


@router.get("/portfolio/edit/row", response_class=HTMLResponse)
def add_holding_row(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/holdings_rows.html", {"h": {}})

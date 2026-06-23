from __future__ import annotations
import json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import build_portfolio_view

router = APIRouter()


@router.get("/portfolio", response_class=HTMLResponse)
def portfolio(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_portfolio_view(conn)
    donut = json.dumps([{"name": h["symbol"], "value": h["market_value"] or 0}
                        for h in view.holdings])
    return templates.TemplateResponse(request, "portfolio.html",
        {"title": "포트폴리오", "view": view, "donut_json": donut})

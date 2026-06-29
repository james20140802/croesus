from __future__ import annotations
import json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import build_asset_detail

router = APIRouter()


@router.get("/asset/{asset_id}", response_class=HTMLResponse)
def asset_detail(request: Request, asset_id: str, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_asset_detail(conn, asset_id)
    if view is None:
        return templates.TemplateResponse(
            request, "asset_detail.html", {"title": "종목", "view": None}, status_code=404)
    price_json = json.dumps(view.price_history)
    return templates.TemplateResponse(
        request, "asset_detail.html",
        {"title": view.symbol, "view": view, "price_json": price_json})

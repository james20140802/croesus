from __future__ import annotations
import json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import build_opportunity_view, build_opportunity_detail

router = APIRouter()


@router.get("/opportunities", response_class=HTMLResponse)
def opportunities(request: Request, gate: str | None = None, db_path=Depends(get_db_path)):
    with get_read_connection(db_path) as conn:
        view = build_opportunity_view(conn, gate)
    scatter = json.dumps([{"symbol": r.symbol, "upside": r.base_upside_pct or 0,
                           "confidence": r.confidence or "", "gate": r.gate_status or "none"}
                          for r in view.rows])
    return templates.TemplateResponse(request, "opportunities.html",
        {"title": "기회", "view": view, "scatter_json": scatter, "gate": gate})


@router.get("/opportunities/{asset_id}", response_class=HTMLResponse)
def opportunity_detail(request: Request, asset_id: str, db_path=Depends(get_db_path)):
    with get_read_connection(db_path) as conn:
        row = build_opportunity_detail(conn, asset_id)
    bands = json.dumps(row.bands) if row else "{}"
    return templates.TemplateResponse(request, "opportunity_detail.html",
        {"title": row.symbol if row else "기회", "row": row, "bands_json": bands})

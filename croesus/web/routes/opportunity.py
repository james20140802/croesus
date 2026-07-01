from __future__ import annotations
import json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import (
    NORMALIZED_METHODOLOGY,
    OPP_METHODOLOGY_CHOICES,
    _resolve_opp_methodology,
    build_opportunity_view,
    build_opportunity_detail,
)

router = APIRouter()


@router.get("/opportunities", response_class=HTMLResponse)
def opportunities(request: Request, gate: str | None = None,
                  methodology: str | None = None, db_path=Depends(get_db_path)):
    method = _resolve_opp_methodology(methodology)
    with get_read_connection(db_path) as conn:
        view = build_opportunity_view(conn, gate, methodology=method)
    if method == NORMALIZED_METHODOLOGY:
        # Ranking metric is the plausibility gap (smaller = cheaper); negate so
        # "more attractive" reads to the right on the same scatter axis.
        scatter = json.dumps([{"symbol": r.symbol,
                               "upside": (-(r.plausibility_gap) * 100) if r.plausibility_gap is not None else 0,
                               "confidence": r.valuation_quality or "",
                               "gate": r.gate_status or "none"} for r in view.rows])
    else:
        scatter = json.dumps([{"symbol": r.symbol, "upside": r.base_upside_pct or 0,
                               "confidence": r.confidence or "", "gate": r.gate_status or "none"}
                              for r in view.rows])
    return templates.TemplateResponse(request, "opportunities.html",
        {"title": "기회", "view": view, "scatter_json": scatter, "gate": gate,
         "methodology": method, "methodologies": OPP_METHODOLOGY_CHOICES})


@router.get("/opportunities/{asset_id}", response_class=HTMLResponse)
def opportunity_detail(request: Request, asset_id: str,
                       methodology: str | None = None, db_path=Depends(get_db_path)):
    method = _resolve_opp_methodology(methodology)
    with get_read_connection(db_path) as conn:
        row = build_opportunity_detail(conn, asset_id, methodology=method)
    if row is None:
        return templates.TemplateResponse(request, "opportunity_detail.html",
            {"title": "기회", "row": None, "bands_json": "{}"}, status_code=404)
    bands = json.dumps(row.bands)
    return templates.TemplateResponse(request, "opportunity_detail.html",
        {"title": row.symbol, "row": row, "bands_json": bands})

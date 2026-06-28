from __future__ import annotations
import json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import build_macro_view

router = APIRouter()


@router.get("/macro", response_class=HTMLResponse)
def macro(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_macro_view(conn)
    history_json = json.dumps(view.history) if view else "[]"
    return templates.TemplateResponse(
        request, "macro.html",
        {"title": "매크로", "view": view, "history_json": history_json},
    )

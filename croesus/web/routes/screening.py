from __future__ import annotations
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import build_screening_view

router = APIRouter()


@router.get("/screening", response_class=HTMLResponse)
def screening(request: Request, bucket: str | None = None, db_path=Depends(get_db_path)):
    with get_read_connection(db_path) as conn:
        view = build_screening_view(conn, bucket)
    template = "partials/screening_table.html" if request.headers.get("hx-request") else "screening.html"
    return templates.TemplateResponse(request, template, {"title": "스크리닝", "view": view, "bucket": bucket})

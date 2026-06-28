from __future__ import annotations
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import build_home_view

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_home_view(conn)
    return templates.TemplateResponse(request, "home.html",
        {"title": "오늘 한눈에", "view": view})

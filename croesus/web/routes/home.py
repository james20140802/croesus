from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "home.html", {"title": "오늘 한눈에"}
    )

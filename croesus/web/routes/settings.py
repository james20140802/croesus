from __future__ import annotations
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection, get_write_connection
from croesus.web.forms import parse_profile_form
from croesus.profiles.repository import ProfileRepository
from croesus.profiles.seed_default_profile import DEFAULT_PROFILE

router = APIRouter()


def _load_profile(conn):
    repo = ProfileRepository(conn)
    try:
        existing = repo.get_profile(DEFAULT_PROFILE.profile_id)
    except Exception:
        existing = None
    return existing or DEFAULT_PROFILE


def _scheduler_status(request: Request) -> dict | None:
    scheduler = getattr(request.app.state, "scheduler", None)
    return scheduler.state.as_dict() if scheduler is not None else None


@router.get("/settings/profile", response_class=HTMLResponse)
def edit_profile(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        profile = _load_profile(conn)
        try:
            targets = ProfileRepository(conn).get_policy_targets(profile.profile_id)
        except Exception:
            targets = []
    return templates.TemplateResponse(request, "settings_profile.html",
        {"title": "프로필 설정", "profile": profile, "targets": targets, "errors": [],
         "scheduler": _scheduler_status(request)})


@router.post("/settings/refresh")
async def refresh_now(request: Request, db_path=Depends(get_db_path)):
    """수동으로 데이터 갱신을 한 번 실행한다(로컬 단일 사용자용)."""
    import asyncio
    from croesus.web.scheduler import _default_refresh

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await scheduler.run_once()
    else:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _default_refresh, str(db_path), print)
    return RedirectResponse("/settings/profile", status_code=303)


@router.post("/settings/profile", response_class=HTMLResponse)
async def save_profile_route(request: Request, db_path=Depends(get_db_path)):
    form = await request.form()
    data = {k: form.getlist(k) if k in ("sleeve_name", "target_weight", "min_weight", "max_weight")
            else form.get(k) for k in form.keys()}
    with get_read_connection(db_path) as conn:
        existing = _load_profile(conn)
    profile, targets, errors = parse_profile_form(data, existing)
    if errors:
        return templates.TemplateResponse(request, "settings_profile.html",
            {"title": "프로필 설정", "profile": profile, "targets": targets, "errors": errors},
            status_code=400)
    with get_write_connection(db_path) as conn:
        ProfileRepository(conn).save_profile(profile, targets)
    return RedirectResponse("/settings/profile", status_code=303)

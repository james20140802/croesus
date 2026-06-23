from __future__ import annotations
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection, get_write_connection
from croesus.web.forms import parse_profile_form
from croesus.web.services import resolve_portfolio_id
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


@router.get("/settings/profile", response_class=HTMLResponse)
def edit_profile(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        profile = _load_profile(conn)
        targets = ProfileRepository(conn).get_policy_targets(profile.profile_id)
    return templates.TemplateResponse(request, "settings_profile.html",
        {"title": "프로필 설정", "profile": profile, "targets": targets, "errors": []})


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

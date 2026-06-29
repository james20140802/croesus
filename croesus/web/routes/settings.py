from __future__ import annotations
import asyncio
import re
from dataclasses import replace
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection, get_write_connection
from croesus.web.forms import parse_profile_form
from croesus.profiles.repository import ProfileRepository
from croesus.profiles.seed_default_profile import DEFAULT_PROFILE
from croesus.profiles.presets import band_by_name, list_presets, preset_label, preset_profile

router = APIRouter()


def _load_profile(conn):
    repo = ProfileRepository(conn)
    try:
        existing = repo.get_profile(DEFAULT_PROFILE.profile_id)
    except Exception:
        existing = None
    return existing or DEFAULT_PROFILE


def _saved_profiles(conn) -> list[dict]:
    """Named user profiles (everything except the active 'default')."""
    try:
        profiles = ProfileRepository(conn).list_profiles()
    except Exception:
        return []
    return [{"id": p.profile_id, "name": p.name}
            for p in profiles if p.profile_id != DEFAULT_PROFILE.profile_id]


def _preset_options() -> list[dict]:
    return [{"value": f"preset:{b.name}", "label": preset_label(b.name)} for b in list_presets()]


def _slugify(name: str) -> str:
    """A stable, unicode-friendly id from a profile name (Korean kept)."""
    slug = re.sub(r"[^\w]+", "-", name.strip().lower(), flags=re.UNICODE).strip("-")
    return f"user-{slug[:48]}" if slug else ""


def _scheduler_status(request: Request) -> dict | None:
    scheduler = getattr(request.app.state, "scheduler", None)
    return scheduler.state.as_dict() if scheduler is not None else None


def _load_draft(conn, load: str):
    """Resolve a ``?load=`` value into a (profile, targets, notice) draft.

    Drafts are in-memory previews — nothing is written until the user Saves.
    Returns (None, None, None) when the value is unknown.
    """
    base = _load_profile(conn)
    if load.startswith("preset:"):
        band = band_by_name(load.split(":", 1)[1])
        if band is None:
            return None, None, None
        profile, targets = preset_profile(band, base)
        return profile, targets, f"‘{preset_label(band.name)}’ 프리셋을 불러왔어요"
    if load.startswith("saved:"):
        pid = load.split(":", 1)[1]
        repo = ProfileRepository(conn)
        profile = repo.get_profile(pid)
        if profile is None:
            return None, None, None
        targets = repo.get_policy_targets(pid)
        return profile, targets, f"‘{profile.name}’ 프로필을 불러왔어요"
    return None, None, None


@router.get("/settings/profile", response_class=HTMLResponse)
def edit_profile(request: Request, load: str | None = None,
                 db_path=Depends(get_db_path)) -> HTMLResponse:
    notice = None
    with get_read_connection(db_path) as conn:
        if load:
            profile, targets, notice = _load_draft(conn, load)
        else:
            profile = targets = None
        if profile is None:
            profile = _load_profile(conn)
            try:
                targets = ProfileRepository(conn).get_policy_targets(profile.profile_id)
            except Exception:
                targets = []
        presets = _preset_options()
        saved = _saved_profiles(conn)
    return templates.TemplateResponse(request, "settings_profile.html",
        {"title": "프로필 설정", "profile": profile, "targets": targets, "errors": [],
         "scheduler": _scheduler_status(request), "presets": presets,
         "saved_profiles": saved, "loaded_notice": notice})


# 스케줄러가 없을 때(수동 갱신만) 동시 실행을 막는 가드. 스케줄러가 있으면
# run_once() 안의 state.running 체크가 같은 역할을 한다.
_manual_refresh_lock = asyncio.Lock()


@router.post("/settings/refresh")
async def refresh_now(request: Request, db_path=Depends(get_db_path)):
    """수동으로 데이터 갱신을 한 번 실행한다(로컬 단일 사용자용)."""
    from croesus.web.scheduler import run_default_refresh

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await scheduler.run_once()
    elif not _manual_refresh_lock.locked():  # 이미 갱신 중이면 조용히 무시(중복 클릭 방지)
        async with _manual_refresh_lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, run_default_refresh, str(db_path), print)
    return RedirectResponse("/settings/profile", status_code=303)


def _parse_form_data(form) -> dict:
    return {k: form.getlist(k) if k in ("sleeve_name", "target_weight", "min_weight", "max_weight")
            else form.get(k) for k in form.keys()}


def _error_response(request, profile, targets, errors, db_path):
    with get_read_connection(db_path) as conn:
        ctx_presets, ctx_saved = _preset_options(), _saved_profiles(conn)
    return templates.TemplateResponse(request, "settings_profile.html",
        {"title": "프로필 설정", "profile": profile, "targets": targets, "errors": errors,
         "scheduler": _scheduler_status(request), "presets": ctx_presets,
         "saved_profiles": ctx_saved, "loaded_notice": None}, status_code=400)


@router.post("/settings/profile", response_class=HTMLResponse)
async def save_profile_route(request: Request, db_path=Depends(get_db_path)):
    form = await request.form()
    data = _parse_form_data(form)
    with get_read_connection(db_path) as conn:
        existing = _load_profile(conn)
    profile, targets, errors = parse_profile_form(data, existing)
    if errors:
        return _error_response(request, profile, targets, errors, db_path)
    with get_write_connection(db_path) as conn:
        ProfileRepository(conn).save_profile(profile, targets)
    return RedirectResponse("/settings/profile", status_code=303)


@router.post("/settings/profile/save-as", response_class=HTMLResponse)
async def save_profile_as_route(request: Request, db_path=Depends(get_db_path)):
    """Save the edited form as a NEW named profile, leaving the active one alone."""
    form = await request.form()
    data = _parse_form_data(form)
    name = (form.get("profile_name") or "").strip()
    with get_read_connection(db_path) as conn:
        existing = _load_profile(conn)
    profile, targets, errors = parse_profile_form(data, existing)
    if not name:
        errors = list(errors) + ["프로필 이름을 입력하세요"]
    new_id = _slugify(name)
    if not new_id and name:
        errors = list(errors) + ["프로필 이름에 사용할 수 있는 문자가 없습니다"]
    if errors:
        return _error_response(request, profile, targets, errors, db_path)
    profile = replace(profile, profile_id=new_id, name=name)
    targets = [replace(t, profile_id=new_id) for t in targets]
    with get_write_connection(db_path) as conn:
        ProfileRepository(conn).save_profile(profile, targets)
    return RedirectResponse(f"/settings/profile?load=saved:{new_id}", status_code=303)


@router.post("/settings/profile/delete", response_class=HTMLResponse)
async def delete_profile_route(request: Request, db_path=Depends(get_db_path)):
    form = await request.form()
    pid = (form.get("profile_id") or "").strip()
    if pid and pid != DEFAULT_PROFILE.profile_id:
        with get_write_connection(db_path) as conn:
            try:
                ProfileRepository(conn).delete_profile(pid)
            except ValueError:
                pass  # default profile guard — nothing to do
    return RedirectResponse("/settings/profile", status_code=303)

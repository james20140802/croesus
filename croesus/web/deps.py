from __future__ import annotations
from pathlib import Path
from fastapi import Request
from fastapi.templating import Jinja2Templates

from croesus.web.labels import JINJA_FILTERS

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.filters.update(JINJA_FILTERS)


def _today_str() -> str:
    from datetime import date
    return date.today().isoformat()


templates.env.globals["today"] = _today_str


def get_db_path(request: Request) -> Path:
    return Path(request.app.state.db_path)

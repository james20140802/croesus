from __future__ import annotations
from pathlib import Path
from fastapi import Request
from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def get_db_path(request: Request) -> Path:
    return Path(request.app.state.db_path)

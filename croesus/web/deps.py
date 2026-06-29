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


_STATIC_DIR = Path(__file__).parent / "static"


def _asset_version(rel: str) -> str:
    """정적 자산 캐시버스팅 버전(파일 수정 시각).

    CSS/JS를 고쳐도 URL이 그대로면 브라우저(특히 폰·태블릿)가 캐시된 옛 파일을
    써서 변경이 안 보인다. 파일이 바뀌면 ``?v=`` 값이 바뀌어 새로 받게 한다.
    """
    try:
        return str(int((_STATIC_DIR / rel).stat().st_mtime))
    except OSError:
        return "0"


templates.env.globals["asset_v"] = _asset_version


def get_db_path(request: Request) -> Path:
    return Path(request.app.state.db_path)

from __future__ import annotations
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from croesus.db.connection import resolve_db_path
from croesus.web.routes import home, macro, screening, portfolio, opportunity

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Croesus Dashboard", docs_url=None, redoc_url=None)
    app.state.db_path = str(resolve_db_path(db_path))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(home.router)
    app.include_router(macro.router)
    app.include_router(screening.router)
    app.include_router(portfolio.router)
    app.include_router(opportunity.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    from croesus.web.db import DataUpdatingError
    from croesus.web.deps import templates

    @app.exception_handler(DataUpdatingError)
    async def _updating_handler(request: Request, exc: DataUpdatingError):
        return templates.TemplateResponse(
            request, "error_updating.html", {"title": "동기화 중"}, status_code=503
        )

    return app

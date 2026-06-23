from __future__ import annotations
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from croesus.db.connection import resolve_db_path
from croesus.web.routes import home

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Croesus Dashboard", docs_url=None, redoc_url=None)
    app.state.db_path = str(resolve_db_path(db_path))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(home.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app

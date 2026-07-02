from __future__ import annotations
import os
from contextlib import asynccontextmanager
from datetime import time
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from croesus.db.connection import resolve_db_path
from croesus.web.routes import home, macro, screening, portfolio, opportunity, settings, asset

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(db_path: str | Path | None = None, *, schedule_at: time | None = None) -> FastAPI:
    from croesus.web.scheduler import DataScheduler

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Bring the DB schema up to date before serving. Without this, a schema
        # addition (a new table like normalized_dcf_snapshots) leaves the page
        # 500ing on an existing DB until someone runs migrate() by hand.
        # schema.sql is idempotent (CREATE TABLE IF NOT EXISTS), so this is safe
        # to run on every startup.
        from croesus.db.migrate import migrate

        migrate(app.state.db_path)

        scheduler = None
        if schedule_at is not None:
            scheduler = DataScheduler(app.state.db_path, schedule_at)
            scheduler.start()
        app.state.scheduler = scheduler
        try:
            yield
        finally:
            if scheduler is not None:
                await scheduler.stop()

    app = FastAPI(title="Croesus Dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.db_path = str(resolve_db_path(db_path))
    app.state.scheduler = None
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(home.router)
    app.include_router(macro.router)
    app.include_router(screening.router)
    app.include_router(portfolio.router)
    app.include_router(opportunity.router)
    app.include_router(settings.router)
    app.include_router(asset.router)

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


def app_factory() -> FastAPI:
    """uvicorn ``--reload`` 전용 진입점.

    reload 모드에서는 uvicorn이 앱을 import string으로 받아 자식 프로세스에서
    인자 없이 호출하므로, 설정은 환경변수로 전달한다(``__main__`` 참조).
    """
    schedule_raw = os.environ.get("CROESUS_SCHEDULE_AT")
    schedule_at = None
    if schedule_raw:
        from croesus.web.scheduler import parse_run_at

        schedule_at = parse_run_at(schedule_raw)
    # db_path는 넘기지 않으면 resolve_db_path가 CROESUS_DB_PATH를 직접 읽는다.
    return create_app(None, schedule_at=schedule_at)

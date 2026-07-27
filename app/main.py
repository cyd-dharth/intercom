import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import close_pool, get_pool, init_pool
from app.errors import AppError, app_error_handler, unhandled_error_handler
from app.logging import configure_logging, log_extra, new_request_id, request_id_var, workspace_id_var
from app.repositories import jobs as jobs_repo
from scripts.migrate import run_migrations

logger = logging.getLogger("app.main")

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIST = BASE_DIR / "web" / "dist"
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    pool = await init_pool()
    await run_migrations(pool)
    async with pool.acquire() as conn:
        reset_count = await jobs_repo.reset_stuck_jobs(conn)
    if reset_count:
        logger.info("reset stuck jobs", extra=log_extra(count=reset_count))

    background_tasks: list[asyncio.Task] = []
    # Phase 2+ will start: hub writer tasks, job worker loops, email poller, snooze sweeper.

    yield

    for task in background_tasks:
        task.cancel()
    if background_tasks:
        await asyncio.wait(background_tasks, timeout=5)
    await close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="Inbox", lifespan=lifespan)

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = new_request_id()
        token = request_id_var.set(request_id)
        ws_token = workspace_id_var.set("-")
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
            workspace_id_var.reset(ws_token)
        response.headers["X-Request-Id"] = request_id
        return response

    from app.api.auth import router as auth_router
    from app.api.team import router as team_router

    app.include_router(auth_router)
    app.include_router(team_router)

    @app.get("/healthz")
    async def healthz():
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("select 1")
            pending_jobs = await jobs_repo.count_pending(conn)
        return {
            "status": "ok",
            "pool": {"size": pool.get_size(), "free": pool.get_idle_size()},
            "pending_jobs": pending_jobs,
        }

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    if WEB_DIST.exists():
        assets_dir = WEB_DIST / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            index = WEB_DIST / "index.html"
            return FileResponse(str(index))

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.app_env == "local")

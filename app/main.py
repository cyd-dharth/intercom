import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    pool = await init_pool()
    await run_migrations(pool)
    async with pool.acquire() as conn:
        reset_count = await jobs_repo.reset_stuck_jobs(conn)
    if reset_count:
        logger.info("reset stuck jobs", extra=log_extra(count=reset_count))

    from app.email.poller import poller_loop
    from app.jobs.worker import worker_loop

    background_tasks: list[asyncio.Task] = []
    for worker_id in range(settings.worker_concurrency):
        background_tasks.append(asyncio.create_task(worker_loop(worker_id)))
    background_tasks.append(asyncio.create_task(poller_loop()))

    yield

    for task in background_tasks:
        task.cancel()
    if background_tasks:
        await asyncio.wait(background_tasks, timeout=5)
    await close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="Intercom", lifespan=lifespan)

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

    @app.middleware("http")
    async def custom_domain_routing_middleware(request: Request, call_next):
        """Host header routing per CLAUDE.md section 12 step 3: on a verified custom
        domain, the apex path serves that workspace's public KB with no path prefix,
        by rewriting the ASGI path to the equivalent /kb/public/{slug}/... route before
        FastAPI's router sees it. Only ever resolves against a verified row, so an
        unverified or spoofed Host header cannot reach another tenant's KB this way."""
        from app.repositories import domains as domains_repo

        host = request.headers.get("host", "").split(":")[0].lower()
        path = request.url.path
        excluded_prefixes = ("/api/", "/static/", "/assets/", "/widget", "/ws/", "/healthz", "/kb/public/")
        if host and not path.startswith(excluded_prefixes):
            pool = get_pool()
            async with pool.acquire() as conn:
                domain = await domains_repo.get_verified_by_hostname(conn, host)
                if domain is not None:
                    from app.repositories import workspaces as workspaces_repo

                    workspace = await workspaces_repo.get_workspace_by_id(conn, domain["workspace_id"])
                    if workspace is not None:
                        slug = workspace["slug"]
                        request.scope["path"] = f"/kb/public/{slug}" if path == "/" else f"/kb/public/{slug}{path}"
        return await call_next(request)

    from app.api.admin import router as admin_router
    from app.api.auth import router as auth_router
    from app.api.conversations import router as conversations_router
    from app.api.domains import router as domains_router
    from app.api.kb import router as kb_router
    from app.api.kb_public import router as kb_public_router
    from app.api.team import router as team_router
    from app.api.widget import router as widget_router
    from app.realtime.routes import router as realtime_router

    app.include_router(auth_router)
    app.include_router(team_router)
    app.include_router(conversations_router)
    app.include_router(kb_router)
    app.include_router(kb_public_router)
    app.include_router(widget_router)
    app.include_router(realtime_router)
    app.include_router(admin_router)
    app.include_router(domains_router)

    @app.get("/widget")
    async def widget_page(request: Request):
        return templates.TemplateResponse(
            "widget.html", {"request": request, "ws_scheme": "wss" if request.url.scheme == "https" else "ws"}
        )

    @app.get("/demo")
    async def demo_page(request: Request):
        from app.repositories import workspaces as workspaces_repo

        pool = get_pool()
        async with pool.acquire() as conn:
            workspace = await workspaces_repo.get_workspace_by_slug(conn, settings.email_fallback_workspace_slug)
        public_key = workspace["public_key"] if workspace else ""
        return templates.TemplateResponse("demo.html", {"request": request, "public_key": public_key})

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
